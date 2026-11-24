"""Fixed local stdio entry point, launched only after sandbox and hash checks."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from storage import data_directory, initialize

initialize(data_directory())
from mcp_server import mcp

mcp.run(transport='stdio')
