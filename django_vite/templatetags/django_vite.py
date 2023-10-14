import json
from pathlib import Path
from typing import Dict, List, Callable, NamedTuple, Optional, Type, Union
from urllib.parse import urljoin

from django import template
from django.apps import apps
from django.conf import settings
from django.utils.safestring import mark_safe

register = template.Library()

DEFAULT_CONFIG_KEY = "default"


class DjangoViteManifest(NamedTuple):
    """
    Represent an entry for a file inside the "manifest.json".
    """

    file: str
    src: str
    isEntry: bool
    css: Optional[List[str]] = []
    imports: Optional[List[str]] = []


class DjangoViteConfig(NamedTuple):
    """
    Represent the Django Vite configuration structure.
    """

    # Location of Vite compiled assets (only used in Vite production mode).
    assets_path: Union[Path, str]

    # If using in development or production mode.
    dev_mode: bool = False

    # Default Vite server protocol (http or https)
    dev_server_protocol: str = "http"

    # Default vite server hostname.
    dev_server_host: str = "localhost"

    # Default Vite server port.
    dev_server_port: int = 3000

    # Default Vite server path to HMR script.
    ws_client_url: str = "@vite/client"

    # Prefix for STATIC_URL.
    static_url_prefix: str = ""

    # Motif in the "manifest.json" to find the polyfills generated by Vite.
    legacy_polyfills_motif: str = "legacy-polyfills"

    # Path to your manifest file generated by Vite.
    manifest_path: Union[Path, str] = ""

    # Default Vite server path to React RefreshRuntime for @vitejs/plugin-react.
    react_refresh_url: str = "@react-refresh"

    @property
    def static_url(self) -> str:
        url = urljoin(settings.STATIC_URL, self.static_url_prefix)
        if not url.endswith("/"):
            url += "/"
        return url

    @property
    def static_root(self) -> Union[Path, str]:
        """
        Compute the static root URL of assets.

        Returns:
            Union[Path, str] -- Static root URL.
        """

        return (
            self.assets_path
            if self.dev_mode
            else Path(settings.STATIC_ROOT) / self.static_url_prefix
        )

    def get_computed_manifest_path(self) -> Union[Path, str]:
        """
        Compute the path to the "manifest.json".

        Returns:
            Union[Path, str] -- Path to the "manifest.json".
        """

        return (
            self.manifest_path
            if self.manifest_path
            else self.static_root / "manifest.json"
        )


class DjangoViteAssetLoader:
    """
    Class handling Vite asset loading.
    """

    _instance = None

    _configs = Dict[str, Type[DjangoViteConfig]]
    _manifests: Dict[str, Type[DjangoViteManifest]]
    _static_urls: Dict[str, str]

    def __init__(self) -> None:
        raise RuntimeError("Use the instance() method instead.")

    def generate_vite_asset(
        self,
        path: str,
        config_key: str,
        **kwargs: Dict[str, str],
    ) -> str:
        """
        Generates a <script> tag for this JS/TS asset, a <link> tag for
        all of its CSS dependencies, and a <link modulepreload>
        for the js dependencies, as listed in the manifest file
        (for production only).
        In development Vite loads all by itself.

        Arguments:
            path {str} -- Path to a Vite JS/TS asset to include.
            config_key {str} -- Key of the configuration to use.

        Returns:
            str -- All tags to import this file in your HTML page.

        Keyword Arguments:
            **kwargs {Dict[str, str]} -- Adds new attributes to generated
                script tags.

        Raises:
            RuntimeError: If cannot find the file path in the
                manifest (only in production).

        Returns:
            str -- The <script> tag and all <link> tags to import
                this asset in your page.
        """

        config = self._get_config(config_key)
        static_url = config.static_url

        if config.dev_mode:
            return DjangoViteAssetLoader._generate_script_tag(
                DjangoViteAssetLoader._generate_vite_server_url(path, config),
                {"type": "module", **kwargs},
            )

        manifest = self._get_manifest(config_key)

        if path not in manifest:
            raise RuntimeError(
                f"Cannot find {path} in Vite manifest "
                f"at {config.get_computed_manifest_path()}"
            )

        tags = []
        scripts_attrs = {"type": "module", "crossorigin": "", **kwargs}

        # Add dependent CSS
        tags.extend(self._load_css_files_of_asset(path, [], config_key))

        # Add the script by itself
        tags.append(
            DjangoViteAssetLoader._generate_script_tag(
                urljoin(static_url, manifest[path].file),
                attrs=scripts_attrs,
            )
        )

        # Preload imports
        preload_attrs = {
            "type": "text/javascript",
            "crossorigin": "anonymous",
            "rel": "modulepreload",
            "as": "script",
        }

        for dep in manifest.imports:
            dep_manifest_entry = self._manifest[dep]
            dep_file = dep_manifest_entry["file"]
            url = DjangoViteAssetLoader._generate_production_server_url(
                dep_file, config.static_url_prefix
            )
            tags.append(
                DjangoViteAssetLoader._generate_preload_tag(
                    url,
                    attrs=preload_attrs,
                )
            )

        return "\n".join(tags)

    def preload_vite_asset(
        self,
        path: str,
        config_key: str = DEFAULT_CONFIG_KEY,
    ) -> str:
        """
        Generates a <link modulepreload> tag for this JS/TS asset, a
        <link preload> tag for all of its CSS dependencies,
        and a <link modulepreload> for the js dependencies.
        In development this template tag renders nothing,
        since files aren't compiled yet"

        Arguments:
            path {str} -- Path to a Vite JS/TS asset to preload.
            config_key {str} -- Key of the configuration to use.

        Returns:
            str -- All tags to preload this file in your HTML page.

        Raises:
            RuntimeError: If cannot find the file path in the
                manifest.

        Returns:
            str -- all <link> tags to preload
                this asset.
        """
        tags = []
        config = self._get_config(config_key)
        manifest = self._get_manifest(config_key)
        manifest_entry = manifest[path]

        if not config.dev_mode:
            return ""

        if path not in manifest:
            raise RuntimeError(
                f"Cannot find {path} in Vite manifest "
                f"at {config.get_computed_manifest_path()}"
            )

        # Add the script by itself
        script_attrs = {
            "type": "text/javascript",
            "crossorigin": "anonymous",
            "rel": "modulepreload",
            "as": "script",
        }

        manifest_file = manifest_entry.file
        url = DjangoViteAssetLoader._generate_production_server_url(
            manifest_file, config.static_url_prefix
        )
        tags.append(
            DjangoViteAssetLoader._generate_preload_tag(
                url,
                attrs=script_attrs,
            )
        )

        # Add dependent CSS
        tags.extend(self._preload_css_files_of_asset(path, [], config_key))

        # Preload imports
        for dependency_path in manifest_entry.imports:
            dependency_file = manifest[dependency_path].file
            url = DjangoViteAssetLoader._generate_production_server_url(
                dependency_file, config.static_url_prefix
            )
            tags.append(
                DjangoViteAssetLoader._generate_preload_tag(
                    url,
                    attrs=script_attrs,
                )
            )

        return "\n".join(tags)

    def _preload_css_files_of_asset(
        self,
        path: str,
        already_processed: List[str],
        config_key: str = DEFAULT_CONFIG_KEY,
    ) -> List[str]:
        return self._generate_css_files_of_asset(
            path,
            already_processed,
            DjangoViteAssetLoader._generate_stylesheet_preload_tag,
            config_key,
        )

    def _load_css_files_of_asset(
        self,
        path: str,
        already_processed: List[str],
        config_key: str = DEFAULT_CONFIG_KEY,
    ) -> List[str]:
        return self._generate_css_files_of_asset(
            path,
            already_processed,
            DjangoViteAssetLoader._generate_stylesheet_tag,
            config_key,
        )

    def _generate_css_files_of_asset(
        self,
        path: str,
        already_processed: List[str],
        tag_generator: Callable,
        config_key: str = DEFAULT_CONFIG_KEY,
    ) -> List[str]:
        """
        Generates all CSS tags for dependencies of an asset.

        Arguments:
            path {str} -- Path to an asset in the 'manifest.json'.
            config_key {str} -- Key of the configuration to use.
            already_processed {list} -- List of already processed CSS file.

        Returns:
            list -- List of CSS tags.
        """

        tags = []
        config = self._get_config(config_key)
        manifest = self._get_manifest(config_key)
        manifest_entry = manifest[path]

        for import_path in manifest_entry.imports:
            tags.extend(
                self._generate_css_files_of_asset(
                    import_path, already_processed, tag_generator, config_key
                )
            )

        for css_path in manifest_entry.css:
            if css_path not in already_processed:
                url = DjangoViteAssetLoader._generate_production_server_url(
                    css_path, config.static_url_prefix
                )
                tags.append(tag_generator(url))

            already_processed.append(css_path)

        return tags

    def generate_vite_asset_url(self, path: str, config_key: str) -> str:
        """
        Generates only the URL of an asset managed by ViteJS.
        Warning, this function does not generate URLs for dependant assets.

        Arguments:
            path {str} -- Path to a Vite asset.
            config_key {str} -- Key of the configuration to use.

        Raises:
            RuntimeError: If cannot find the asset path in the
                manifest (only in production).

        Returns:
            str -- The URL of this asset.
        """

        config = self._get_config(config_key)

        if config.dev_mode:
            return DjangoViteAssetLoader._generate_vite_server_url(path, config)

        manifest = self._get_manifest(config_key)

        if path not in manifest:
            raise RuntimeError(
                f"Cannot find {path} in Vite manifest "
                f"at {config.get_computed_manifest_path()}"
            )

        return DjangoViteAssetLoader._generate_production_server_url(
            self._manifest[path]["file"], config.static_url_prefix
        )

    def generate_vite_legacy_polyfills(
        self,
        config_key: str,
        **kwargs: Dict[str, str],
    ) -> str:
        """
        Generates a <script> tag to the polyfills
        generated by '@vitejs/plugin-legacy' if used.
        This tag must be included at end of the <body> before
        including other legacy scripts.

        Arguments:
            config_key {str} -- Key of the configuration to use.

        Keyword Arguments:
            **kwargs {Dict[str, str]} -- Adds new attributes to generated
                script tags.

        Raises:
            RuntimeError: If polyfills path not found inside
                the 'manifest.json' (only in production).

        Returns:
            str -- The script tag to the polyfills.
        """

        config = self._get_config(config_key)
        manifest = self._get_manifest(config_key)

        if config.dev_mode:
            return ""

        scripts_attrs = {"nomodule": "", "crossorigin": "", **kwargs}

        for path, content in manifest.items():
            if config.legacy_polyfills_motif in path:
                return DjangoViteAssetLoader._generate_script_tag(
                    DjangoViteAssetLoader._generate_production_server_url(
                        content["file"], config.static_url_prefix
                    ),
                    attrs=scripts_attrs,
                )

        raise RuntimeError(
            f"Vite legacy polyfills not found in manifest "
            f"at {config.get_computed_manifest_path()}"
        )

    def generate_vite_legacy_asset(
        self,
        path: str,
        config_key: str,
        **kwargs: Dict[str, str],
    ) -> str:
        """
        Generates a <script> tag for legacy assets JS/TS
        generated by '@vitejs/plugin-legacy'
        (in production only, in development do nothing).

        Arguments:
            path {str} -- Path to a Vite asset to include
                (must contains '-legacy' in its name).
            config_key {str} -- Key of the configuration to use.

        Keyword Arguments:
            **kwargs {Dict[str, str]} -- Adds new attributes to generated
                script tags.

        Raises:
            RuntimeError: If cannot find the asset path in the
                manifest (only in production).

        Returns:
            str -- The script tag of this legacy asset .
        """

        config = self._get_config(config_key)

        if config.dev_mode:
            return ""

        manifest = self._get_manifest(config_key)

        if path not in manifest:
            raise RuntimeError(
                f"Cannot find {path} in Vite manifest "
                f"at {config.get_computed_manifest_path()}"
            )

        scripts_attrs = {"nomodule": "", "crossorigin": "", **kwargs}

        url = DjangoViteAssetLoader._generate_production_server_url(
            manifest[path].file, config.static_url_prefix
        )
        return DjangoViteAssetLoader._generate_script_tag(
            url,
            attrs=scripts_attrs,
        )

    def _get_config(self, config_key: str) -> Type[DjangoViteConfig]:
        """
        Get configuration object registered with the key passed in
        parameter.

        Arguments:
            config_key {str} -- Key of the configuration to retrieve.

        Raises:
            RuntimeError: If no configuration exists for this key.

        Returns:
            Type[DjangoViteConfig] -- The configuration.
        """

        if config_key not in self._configs:
            raise RuntimeError(f'Cannot find "{config_key}" configuration')

        return self._configs[config_key]

    def _parse_manifest(
        self, config_key: str
    ) -> Dict[str, Type[DjangoViteManifest]]:
        """
        Read and parse the Vite manifest file.

        Arguments:
            config_key {str} -- Key of the configuration to use.

        Raises:
            RuntimeError: if cannot load the file or JSON in file is malformed.
        """

        config = self._get_config(config_key)

        try:
            with open(config.get_computed_manifest_path(), "r") as manifest_file:
                manifest_content = manifest_file.read()
                manifest_json = json.loads(manifest_content)

                return {k: DjangoViteManifest(**v) for k, v in manifest_json.items()}

        except Exception as error:
            raise RuntimeError(
                f"Cannot read Vite manifest file at "
                f"{config.get_computed_manifest_path()} : {str(error)}"
            )

    def _get_manifest(self, config_key: str) -> Dict[str, Type[DjangoViteManifest]]:
        """
        Load if needed and parse the "manifest.json" of the specified
        configuration.

        Arguments:
            config_key {str} -- Key of the configuration to use.

        Returns:
            Dict[str, Type[DjangoViteManifest]] -- Parsed content of
                the "manifest.json"
        """

        if config_key not in self._manifests:
            self._manifests[config_key] = self._parse_manifest(config_key)

        return self._manifests[config_key]

    @classmethod
    def instance(cls):
        """
        Singleton.
        Uses singleton to keep parsed manifests in memory after
        the first time they are loaded.

        Returns:
            DjangoViteAssetLoader -- only instance of the class.
        """

        if cls._instance is None:
            cls._instance = cls.__new__(cls)
            cls._instance._configs = {}
            cls._instance._manifests = {}
            cls._instance._static_urls = {}

            if hasattr(settings, "DJANGO_VITE"):
                config = getattr(settings, "DJANGO_VITE")

                for config_key, config_values in config.items():
                    if isinstance(config_values, DjangoViteConfig):
                        cls._instance._configs[config_key] = config_values
                    elif isinstance(config_values, dict):
                        cls._instance._configs[config_key] = DjangoViteConfig(
                            **config_values
                        )
                    else:
                        raise RuntimeError(
                            f"Cannot read configuration for key '{config_key}'"
                        )
            else:
                # Warning : This branch will be remove in further
                # releases. Please use new way of handling configuration.

                _config_keys = {
                    "DJANGO_VITE_DEV_MODE": "dev_mode",
                    "DJANGO_VITE_DEV_SERVER_PROTOCOL": "dev_server_protocol",
                    "DJANGO_VITE_DEV_SERVER_HOST": "dev_server_host",
                    "DJANGO_VITE_DEV_SERVER_PORT": "dev_server_port",
                    "DJANGO_VITE_WS_CLIENT_URL": "ws_client_url",
                    "DJANGO_VITE_ASSETS_PATH": "assets_path",
                    "DJANGO_VITE_STATIC_URL_PREFIX": "static_url_prefix",
                    "DJANGO_VITE_MANIFEST_PATH": "manifest_path",
                    "DJANGO_VITE_LEGACY_POLYFILLS_MOTIF": "legacy_polyfills_motif",
                }

                config = {
                    _config_keys[setting_key]: getattr(settings, setting_key)
                    for setting_key in dir(settings)
                    if setting_key in _config_keys.keys()
                }

                cls._instance._configs[DEFAULT_CONFIG_KEY] = DjangoViteConfig(
                    **config
                )

        return cls._instance

    @classmethod
    def generate_vite_ws_client(
        cls, config_key: str = DEFAULT_CONFIG_KEY, **kwargs: Dict[str, str]
    ) -> str:
        """
        Generates the script tag for the Vite WS client for HMR.
        Only used in development, in production this method returns
        an empty string.

        Arguments:
            config_key {str} -- Key of the configuration to use.

        Returns:
            str -- The script tag or an empty string.

        Keyword Arguments:
            **kwargs {Dict[str, str]} -- Adds new attributes to generated
                script tags.
        """

        config = cls._get_config(config_key)

        if not config.dev_mode:
            return ""

        return cls._generate_script_tag(
            cls._generate_vite_server_url(config.ws_client_url, config),
            {"type": "module", **kwargs},
        )

    @staticmethod
    def _generate_script_tag(src: str, attrs: Dict[str, str]) -> str:
        """
        Generates an HTML script tag.

        Arguments:
            src {str} -- Source of the script.

        Keyword Arguments:
            attrs {Dict[str, str]} -- List of custom attributes
                for the tag.

        Returns:
            str -- The script tag.
        """

        attrs_str = " ".join([f'{key}="{value}"' for key, value in attrs.items()])

        return f'<script {attrs_str} src="{src}"></script>'

    @staticmethod
    def _generate_stylesheet_tag(href: str) -> str:
        """
        Generates an HTML <link> stylesheet tag for CSS.

        Arguments:
            href {str} -- CSS file URL.

        Returns:
            str -- CSS link tag.
        """

        return f'<link rel="stylesheet" href="{href}" />'

    def _generate_stylesheet_preload_tag(href: str) -> str:
        """
        Generates an HTML <link> preload tag for CSS.

        Arguments:
            href {str} -- CSS file URL.

        Returns:
            str -- CSS link tag.
        """

        return f'<link rel="preload" href="{href}" as="style" />'

    @staticmethod
    def _generate_preload_tag(href: str, attrs: Dict[str, str]) -> str:
        attrs_str = " ".join([f'{key}="{value}"' for key, value in attrs.items()])

        return f'<link href="{href}" {attrs_str} />'

    @staticmethod
    def _generate_vite_server_url(
        path: str,
        config: Type[DjangoViteConfig],
    ) -> str:
        """
        Generates an URL to and asset served by the Vite development server.

        Keyword Arguments:
            path {str} -- Path to the asset.
            config {Type[DjangoViteConfig]} -- Config object to use.

        Returns:
            str -- Full URL to the asset.
        """

        return urljoin(
            f"{config.dev_server_protocol}://"
            f"{config.dev_server_host}:{config.dev_server_port}",
            urljoin(config.static_url, path),
        )

    @classmethod
    def generate_vite_react_refresh_url(
        self, config_key: str = DEFAULT_CONFIG_KEY
    ) -> str:
        """
        Generates the script for the Vite React Refresh for HMR.
        Only used in development, in production this method returns
        an empty string.

        Returns:
            str -- The script or an empty string.
            config_key {str} -- Key of the configuration to use.
        """
        config = self._get_config(config_key)

        if not config.dev_mode:
            return ""

        return f"""<script type="module">
            import RefreshRuntime from \
            '{self._generate_vite_server_url(config.react_refresh_url, config)}'
            RefreshRuntime.injectIntoGlobalHook(window)
            window.$RefreshReg$ = () => {{}}
            window.$RefreshSig$ = () => (type) => type
            window.__vite_plugin_react_preamble_installed__ = true
        </script>"""

    @staticmethod
    def _generate_production_server_url(path: str, static_url_prefix="") -> str:
        """
        Generates an URL to an asset served during production.

        Keyword Arguments:
            path {str} -- Path to the asset.

        Returns:
            str -- Full URL to the asset.
        """

        production_server_url = path
        if prefix := static_url_prefix:
            if not static_url_prefix.endswith("/"):
                prefix += "/"
            production_server_url = urljoin(prefix, path)

        if apps.is_installed("django.contrib.staticfiles"):
            from django.contrib.staticfiles.storage import staticfiles_storage

            return staticfiles_storage.url(production_server_url)

        return production_server_url


@register.simple_tag
@mark_safe
def vite_hmr_client(
    config_key: str = DEFAULT_CONFIG_KEY, **kwargs: Dict[str, str]
) -> str:
    """
    Generates the script tag for the Vite WS client for HMR.
    Only used in development, in production this method returns
    an empty string.

    Arguments:
        config {str} -- Configuration to use.

    Returns:
        str -- The script tag or an empty string.

    Keyword Arguments:
        **kwargs {Dict[str, str]} -- Adds new attributes to generated
            script tags.
    """

    return DjangoViteAssetLoader.generate_vite_ws_client(config_key, **kwargs)


@register.simple_tag
@mark_safe
def vite_asset(
    path: str,
    config_key: str = DEFAULT_CONFIG_KEY,
    **kwargs: Dict[str, str],
) -> str:
    """
    Generates a <script> tag for this JS/TS asset, a <link> tag for
    all of its CSS dependencies, and a <link rel="modulepreload">
    for all js dependencies, as listed in the manifest file
    In development Vite loads all by itself.

    Arguments:
        path {str} -- Path to a Vite JS/TS asset to include.
        config {str} -- Configuration to use.

    Returns:
        str -- All tags to import this file in your HTML page.

    Keyword Arguments:
        **kwargs {Dict[str, str]} -- Adds new attributes to generated
            script tags.

    Raises:
        RuntimeError: If cannot find the file path in the
            manifest (only in production).

    Returns:
        str -- The <script> tag and all <link> tags to import this
            asset in your page.
    """

    assert path is not None
    assert config_key is not None

    return DjangoViteAssetLoader.instance().generate_vite_asset(
        path, config_key, **kwargs
    )


@register.simple_tag
@mark_safe
def vite_preload_asset(path: str, config_key: str = DEFAULT_CONFIG_KEY) -> str:
    """
    Generates preloadmodule tag for this JS/TS asset and preloads
    all of its CSS and JS dependencies by reading the manifest
    file (for production only).
    In development does nothing.

    Arguments:
        path {str} -- Path to a Vite JS/TS asset to include.

    Returns:
        str -- All tags to import this file in your HTML page.

    Raises:
        RuntimeError: If cannot find the file path in the
            manifest (only in production).

    """

    assert path is not None

    return DjangoViteAssetLoader.instance().preload_vite_asset(path, config_key)


@register.simple_tag
def vite_asset_url(path: str, config_key: str = DEFAULT_CONFIG_KEY) -> str:
    """
    Generates only the URL of an asset managed by ViteJS.
    Warning, this function does not generate URLs for dependant assets.

    Arguments:
        path {str} -- Path to a Vite asset.
        config {str} -- Configuration to use.

    Raises:
        RuntimeError: If cannot find the asset path in the
            manifest (only in production).

    Returns:
        str -- The URL of this asset.
    """
    assert path is not None
    return DjangoViteAssetLoader.instance().generate_vite_asset_url(path, config_key)


@register.simple_tag
@mark_safe
def vite_legacy_polyfills(
    config_key: str = DEFAULT_CONFIG_KEY, **kwargs: Dict[str, str]
) -> str:
    """
    Generates a <script> tag to the polyfills generated
    by '@vitejs/plugin-legacy' if used.
    This tag must be included at end of the <body> before including
    other legacy scripts.

    Arguments:
        config_key {str} -- Configuration to use.

    Keyword Arguments:
        **kwargs {Dict[str, str]} -- Adds new attributes to generated
            script tags.

    Raises:
        RuntimeError: If polyfills path not found inside
            the 'manifest.json' (only in production).

    Returns:
        str -- The script tag to the polyfills.
    """
    return DjangoViteAssetLoader.instance().generate_vite_legacy_polyfills(
        config_key, **kwargs
    )


@register.simple_tag
@mark_safe
def vite_legacy_asset(
    path: str,
    config_key: str = DEFAULT_CONFIG_KEY,
    **kwargs: Dict[str, str],
) -> str:
    """
    Generates a <script> tag for legacy assets JS/TS
    generated by '@vitejs/plugin-legacy'
    (in production only, in development do nothing).

    Arguments:
        path {str} -- Path to a Vite asset to include
            (must contains '-legacy' in its name).
        config_key {str} -- Configuration to use.

    Keyword Arguments:
        **kwargs {Dict[str, str]} -- Adds new attributes to generated
            script tags.

    Raises:
        RuntimeError: If cannot find the asset path in
            the manifest (only in production).

    Returns:
        str -- The script tag of this legacy asset.
    """

    assert path is not None

    return DjangoViteAssetLoader.instance().generate_vite_legacy_asset(
        path, config_key, **kwargs
    )


@register.simple_tag
@mark_safe
def vite_react_refresh(config_key: str = DEFAULT_CONFIG_KEY) -> str:
    """
    Generates the script for the Vite React Refresh for HMR.
    Only used in development, in production this method returns
    an empty string.

    Returns:
        str -- The script or an empty string.
    """
    return DjangoViteAssetLoader.generate_vite_react_refresh_url(config_key)
