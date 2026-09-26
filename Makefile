test:
	PYTHONPATH=. python -m pytest -q

run:
	PYTHONPATH=. python -m rubbercure --config config/rubbercure.yaml serve --host 127.0.0.1 --port 8080

demo:
	PYTHONPATH=. python -m rubbercure --config config/rubbercure.yaml demo --fast --cycles 12

deadcode:
	PYTHONPATH=. python tools/deadcode.py rubbercure
