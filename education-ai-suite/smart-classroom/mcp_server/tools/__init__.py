from mcp_server.tools.report_tools import register_report_tools
from mcp_server.tools.grading_tools import register_grading_tools


def register_all_tools(mcp):
    register_report_tools(mcp)
    register_grading_tools(mcp)
