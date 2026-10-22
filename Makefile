setup:
	bash scripts/create_env.sh

align:
	.venv/bin/python experiments/two_mirror_alignment.py

check:
	PYTHONPATH=src:vendor/agnes-mhs/src .venv/bin/python -m pytest tests/test_physics.py tests/test_optimizer.py -q
