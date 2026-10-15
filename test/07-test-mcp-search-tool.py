import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.query_process.agent.nodes.node_web_search_mcp import _build_search_arguments


class FakeTool:
    name = "web_search"
    input_schema = {
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer"},
        }
    }


class McpSearchToolTest(unittest.TestCase):
    def test_builds_arguments_from_tool_schema(self):
        self.assertEqual(
            _build_search_arguments(FakeTool(), "数字证书解决方案"),
            {"query": "数字证书解决方案", "limit": 5},
        )


if __name__ == "__main__":
    unittest.main()
