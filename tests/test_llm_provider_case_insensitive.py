import surf


def test_llm_provider_override_is_case_insensitive(tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text(
        "\n".join(
            [
                "[LLM]",
                "provider = L1",
                "",
                "[LLM.L1]",
                "base_url = https://example.com/v1",
                "api_key = test-key",
                "model = test-model",
            ]
        ),
        encoding="utf-8",
    )

    config = surf.Config(str(config_path))

    llm_config = config.get_llm_config("l1")

    assert llm_config["base_url"] == "https://example.com/v1"
    assert llm_config["api_key"] == "test-key"
    assert llm_config["model"] == "test-model"


def test_default_llm_provider_name_is_case_insensitive(tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text(
        "\n".join(
            [
                "[LLM]",
                "provider = l1",
                "",
                "[LLM.L1]",
                "base_url = https://example.com/v1",
                "api_key = test-key",
                "model = test-model",
            ]
        ),
        encoding="utf-8",
    )

    config = surf.Config(str(config_path))

    llm_config = config.get_llm_config()

    assert llm_config["model"] == "test-model"
