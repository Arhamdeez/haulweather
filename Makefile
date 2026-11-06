.PHONY: test backend frontend

PY := .venv/bin/python

test:
	cd backend && ../$(PY) manage.py test routing

backend:
	cd backend && ../$(PY) manage.py runserver 127.0.0.1:8000

frontend:
	cd frontend && npm run dev
