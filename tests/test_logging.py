"""Test logging functionality."""

import os
import sys
import tempfile
import subprocess
import json
import time
import logging

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jsonfs import setup_logging


def test_setup_logging_to_file():
    """Test that setup_logging can be configured for file output."""
    # We can't easily test actual file creation because basicConfig 
    # can only be called once per process. Instead, test the function
    # exists and accepts the right parameters.
    
    # Import the function to ensure it exists
    from jsonfs import setup_logging
    
    # Test that it returns a logger
    logger = setup_logging(logging.DEBUG, log_to_stdout=True)
    assert logger is not None
    assert isinstance(logger, logging.Logger)
    
    # Clean up
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)


def test_setup_logging_to_stdout():
    """Test that setup_logging creates stdout logger."""
    # This should create a stdout logger
    logger = setup_logging(logging.INFO, log_to_stdout=True)
    
    # Check that we have a StreamHandler
    has_stream_handler = any(
        isinstance(h, logging.StreamHandler) 
        for h in logging.root.handlers
    )
    assert has_stream_handler
    
    # Clean up
    for handler in logging.root.handlers[:]:
        handler.close()
        logging.root.removeHandler(handler)


def test_file_logging_via_subprocess():
    """Test file-based logging through subprocess to avoid basicConfig issues."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a test JSON file
        test_json = os.path.join(tmpdir, "test.json")
        with open(test_json, 'w') as f:
            json.dump([{"name": "/", "contents": []}], f)
        
        # Create a test script that uses file logging
        test_script = os.path.join(tmpdir, "test_logging.py")
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(test_script, 'w') as f:
            f.write(f"""
import sys
import os
sys.path.insert(0, '{project_root}')
from jsonfs import setup_logging
import logging

# Setup file logging
logger = setup_logging(logging.INFO, log_to_stdout=False)
logger.info("Test message from file logging")

# Check the log file was created
assert os.path.exists('jsonfs.log'), "jsonfs.log was not created"

# Read and verify content
with open('jsonfs.log', 'r') as f:
    content = f.read()
    assert "Test message from file logging" in content, "Log message not found"

print("File logging test passed")
""")
        
        # Run the test script
        result = subprocess.run(
            [sys.executable, test_script],
            cwd=tmpdir,
            capture_output=True,
            text=True
        )
        
        assert result.returncode == 0, f"Test script failed: {result.stderr}"
        assert "File logging test passed" in result.stdout
        
        # Verify the log file was created
        log_file = os.path.join(tmpdir, "jsonfs.log")
        assert os.path.exists(log_file)




if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])