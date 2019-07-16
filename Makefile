init:
	pip install -r requirements.txt

test:
	py.test

flake8:
	flake8

check_sort:
	isort --recursive --check-only --diff multifield_batch_update/ tests/

fix_sort:
	isort --recursive multifield_batch_update/ tests/

release:
	./release_anki21.sh

readme_to_html:
	grip --export

release_ankiaddon: release
	cp target/anki_multifield_batch_update.zip target/anki_multifield_batch_update.ankiaddon

ci: flake8 check_sort release release_ankiaddon