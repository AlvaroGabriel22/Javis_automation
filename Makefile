SHELL := /bin/bash
.DEFAULT_GOAL := help
PY := backend/.venv/bin/python
ALEMBIC := $(PY) -m alembic -c backend/alembic.ini
NPM := npm --prefix frontend
.PHONY: help install setup build db migrate login-code doctor run demo demo-code demo-doctor check test browser-test chat demo-chat

help:
	@echo 'make demo-chat   Inicia painel, WhatsApp e IA em perfil SQLite.'
	@echo 'make chat        Inicia o mesmo conjunto com PostgreSQL.'
	@echo 'make install     Cria a virtualenv, instala os locks e compila o painel.'
	@echo 'make demo        Inicia demonstração SQLite. Em outro terminal: make demo-code.'
	@echo 'make setup db migrate run   Prepara e inicia o perfil PostgreSQL.'
	@echo 'make check test  Verifica código e testes.'
	@echo 'make chat-doctor  Testa uma saudação no provedor de conversa configurado.'
	@echo 'make agent-doctor Verifica disponibilidade de conversa, visão e busca.'

install:
	python3.12 -m venv backend/.venv
	$(PY) -m pip install -r backend/requirements.lock
	$(PY) -m pip install --no-deps --no-build-isolation -e backend
	$(NPM) ci
	$(MAKE) build

setup:
	@$(PY) scripts/configure.py

build:
	$(NPM) run build

db:
	docker compose --env-file .env -f infra/compose.yaml up -d --wait postgres

migrate:
	$(ALEMBIC) upgrade head

login-code:
	@$(PY) scripts/panel.py login-code

doctor:
	@$(PY) scripts/panel.py doctor

run:
	@$(PY) scripts/panel.py serve

demo:
	@$(PY) scripts/panel.py serve --demo

demo-code:
	@$(PY) scripts/panel.py login-code --demo

demo-doctor:
	@$(PY) scripts/panel.py doctor --demo

check:
	backend/.venv/bin/ruff check backend/src backend/tests backend/migrations scripts
	cd backend && .venv/bin/pyright --pythonpath .venv/bin/python
	$(NPM) run check

test:
	$(PY) -m pytest backend/tests
	$(NPM) test

browser-test:
	$(NPM) run test:e2e

chat:
	@$(PY) scripts/conversation.py

demo-chat:
	@$(PY) scripts/conversation.py --demo

.PHONY: agent-doctor chat-doctor
chat-doctor:
	@$(PY) scripts/agent_doctor.py --chat-only --smoke

agent-doctor:
	@$(PY) scripts/agent_doctor.py

.PHONY: voice
voice:
	python3.12 -m venv backend/.voice
	backend/.voice/bin/python -m pip install -r backend/voice.lock
	backend/.voice/bin/python scripts/prepare_voice.py

.PHONY: google-connect demo-google-connect
google-connect:
	JARVIS_PROFILE=local $(PY) -m jarvis.entrypoints.calendar --connect

demo-google-connect:
	JARVIS_PROFILE=demo $(PY) -m jarvis.entrypoints.calendar --connect
