from lts.slug import slugify_project


def test_camelcase_matches_basic_memory():
    # observed real behaviour: Basic Memory registers `LetTheAISleep` as `let-the-aisleep`
    assert slugify_project("LetTheAISleep") == "let-the-aisleep"


def test_already_slug_passes_through():
    assert slugify_project("netprint") == "netprint"
    assert slugify_project("my-memory") == "my-memory"
    assert slugify_project("lts-default") == "lts-default"


def test_separators_and_edges():
    assert slugify_project("My Project") == "my-project"
    assert slugify_project("_Weird__Name_") == "weird-name"
    assert slugify_project("Cart2Service") == "cart2-service"