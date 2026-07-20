from typing import Any

from dify_plugin import ToolProvider


class HtmlOfflineExporterProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        # No credentials needed: the plugin runs fully offline against bundled vendor files.
        return
