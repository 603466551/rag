import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.query_process.agent.nodes.node_web_search_mcp import _build_search_arguments


class McpSearchArgumentsTest(unittest.TestCase):
    def test_supports_mcp_camel_case_schema_and_query_key(self):
        tool = SimpleNamespace(
            name="search_internet_with_ai",
            inputSchema={
                "properties": {
                    "Query": {"type": "string"},
                    "Mode": {"type": "string"},
                }
            },
        )

        arguments = _build_search_arguments(tool, "测试查询")

        self.assertEqual(arguments, {"Query": "测试查询"})


if __name__ == "__main__":
    unittest.main()
