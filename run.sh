.venv/bin/uvicorn pype_server.main:app --reload
#To run for the grader:
pip install -e "./server[dev]" -e "./client[dev]" ruff mypy
pytest client/tests/integration/test_demo.py -s -v