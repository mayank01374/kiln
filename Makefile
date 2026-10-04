.PHONY: install test demo api
install:
	python -m pip install -e '.[dev]'
test:
	pytest
demo:
	kiln demo
api:
	uvicorn kiln.api:app --reload
