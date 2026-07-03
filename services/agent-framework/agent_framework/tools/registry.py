import yaml
import pathlib
import logging
from langchain_core.tools import BaseTool
from typing import Optional

logger = logging.getLogger(__name__)

ToolRegistry = dict[str, BaseTool]


def load_tool_registry(catalog_path: str) -> Optional[ToolRegistry]:
    """
    Loads catalog.yaml and returns a dict mapping tool name → LangChain BaseTool.
    Returns None (and logs ERROR) on any failure.
    """
    try:
        data = yaml.safe_load(pathlib.Path(catalog_path).read_text())
        tools_data = data.get("tools", [])
        if not tools_data:
            logger.error(f"Tool catalog is empty: {catalog_path}")
            return None

        from agent_framework.tools.calculator import calculator
        from agent_framework.tools.get_time import get_current_time
        from agent_framework.tools.web_search import web_search

        impl_map: dict[str, BaseTool] = {
            "calculator": calculator,
            "get_current_time": get_current_time,
            "web_search": web_search,
        }

        registry: ToolRegistry = {}
        for entry in tools_data:
            name = entry.get("name")
            description = entry.get("description")
            if not name or not description:
                logger.error(
                    f"Tool catalog entry missing 'name' or 'description': {entry}"
                )
                return None
            if name not in impl_map:
                logger.error(
                    f"Tool '{name}' declared in catalog has no implementation"
                )
                return None
            registry[name] = impl_map[name]

        logger.info("Tool registry loaded", extra={"extra_fields": {
            "tools": list(registry.keys()),
        }})
        return registry

    except FileNotFoundError:
        logger.error(f"Tool catalog file not found: {catalog_path}")
    except yaml.YAMLError as exc:
        logger.error(f"Malformed tool catalog YAML at {catalog_path}: {exc}")
    return None
