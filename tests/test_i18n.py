from omge import cli, i18n


def test_cli_message_follows_the_locale(capsys, monkeypatch):
    ko = i18n.CATALOGS["ko"]["cli.no_open"]
    assert ko != i18n.EN["cli.no_open"]
    cli.main(["status"])
    assert capsys.readouterr().out.strip() == i18n.EN["cli.no_open"]
    monkeypatch.setenv("LANG", "ko_KR.UTF-8")
    cli.main(["status"])
    assert capsys.readouterr().out.strip() == ko
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("OMGE_LANG", "ko")
    cli.main(["status"])
    assert capsys.readouterr().out.strip() == ko


def test_every_ko_key_exists_in_en():
    assert set(i18n.CATALOGS["ko"]) <= set(i18n.EN)
