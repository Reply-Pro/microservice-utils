from copy import copy
from unittest import mock

from microservice_utils.openai.adapters import (
    FakeOpenAiLlm,
    MaskedMessages,
    OpenAiChatMessage,
    OpenAiLlm,
)


def test_open_ai_llm_get_masked_chat_messages():
    """Test the method that the OpenAI adapter uses to mask chat messages. The masker
    should be able to unmask responses with masks from several messages."""

    messages = [
        OpenAiChatMessage(
            content="You are a friendly virtual assistant.", role="system"
        ),
        OpenAiChatMessage(
            content="Hi, my name is Marco. I am looking for a gift for my wife.",
            role="user",
        ),
        OpenAiChatMessage(
            content="Sure, Marco. I can help you find a gift for your wife. What's her "
            "name?",
            role="assistant",
        ),
        OpenAiChatMessage(content="Her name is Bianca.", role="user"),
        OpenAiChatMessage(
            content="For Bianca, I suggest a pair of silver earrings.", role="assistant"
        ),
        OpenAiChatMessage(
            content="Thanks! I'm sure Bianca will love that. Can you write me a sweet "
            "note for her?",
            role="user",
        ),
    ]

    # Ensure messages are properly masked
    expected_messages = [
        {"content": "You are a friendly virtual assistant.", "role": "system"},
        {
            "content": "Hi, my name is <NamesMask_1>. I am looking for a gift for "
            "my wife.",
            "role": "user",
        },
        {
            "content": "Sure, <NamesMask_1>. I can help you find a gift for your wife. "
            "What's her name?",
            "role": "assistant",
        },
        {"content": "Her name is <NamesMask_2>.", "role": "user"},
        {
            "content": "For <NamesMask_2>, I suggest a pair of silver earrings.",
            "role": "assistant",
        },
        {
            "content": "Thanks! I'm sure <NamesMask_2> will love that. Can you write "
            "me a sweet note for her?",
            "role": "user",
        },
    ]

    masked_messages = OpenAiLlm.get_masked_chat_messages(messages)

    assert masked_messages.messages == expected_messages

    # Now, unmask the response
    masked_response = "Dear <NamesMask_2>, you are the best. May you shine every day!"

    assert (
        masked_messages.masker.unmask_data(masked_response)
        == "Dear Bianca, you are the best. May you shine every day!"
    )


def test_open_ai_llm_defaults_to_openai_when_base_url_omitted():
    """No base_url -> pass None through, so the SDK falls back to its own default
    (OpenAI's API) and existing OpenAI-direct callers are unaffected."""
    with mock.patch("microservice_utils.openai.adapters.OpenAI") as mock_openai_cls:
        OpenAiLlm(api_key="sk-test")

    mock_openai_cls.assert_called_once_with(api_key="sk-test", base_url=None)


def test_open_ai_llm_passes_base_url_to_client():
    """A base_url is enough to point this adapter at any OpenAI-compatible gateway
    (e.g. OpenRouter's "https://openrouter.ai/api/v1") without any other change."""
    with mock.patch("microservice_utils.openai.adapters.OpenAI") as mock_openai_cls:
        OpenAiLlm(api_key="sk-test", base_url="https://openrouter.ai/api/v1")

    mock_openai_cls.assert_called_once_with(
        api_key="sk-test", base_url="https://openrouter.ai/api/v1"
    )


def test_open_ai_llm_generate_chat_response_calls_client_and_unmasks_result():
    passthrough_masker = mock.Mock()
    passthrough_masker.unmask_data.side_effect = lambda text: text.replace(
        "<NamesMask_1>", "Marco"
    )

    with mock.patch(
        "microservice_utils.openai.adapters.OpenAI"
    ) as mock_openai_cls, mock.patch.object(
        OpenAiLlm,
        "get_masked_chat_messages",
        return_value=MaskedMessages(
            masker=passthrough_masker,
            messages=[{"content": "Hi, I'm <NamesMask_1>.", "role": "user"}],
        ),
    ):
        mock_client = mock_openai_cls.return_value
        mock_response = mock.Mock()
        mock_response.choices = [
            mock.Mock(message=mock.Mock(content="Hello <NamesMask_1>!"))
        ]
        mock_client.chat.completions.create.return_value = mock_response

        adapter = OpenAiLlm(
            api_key="sk-test",
            base_url="https://openrouter.ai/api/v1",
            model="google/gemini-2.5-flash",
        )
        response = adapter.generate_chat_response(
            [OpenAiChatMessage(role="user", content="Hi, I'm Marco.")]
        )

    mock_client.chat.completions.create.assert_called_once_with(
        model="google/gemini-2.5-flash",
        messages=[{"content": "Hi, I'm <NamesMask_1>.", "role": "user"}],
        temperature=0,
        max_tokens=2049,
    )
    passthrough_masker.unmask_data.assert_called_once_with("Hello <NamesMask_1>!")
    assert response == OpenAiChatMessage(role="assistant", content="Hello Marco!")


def test_open_ai_llm_generate_response_uses_completions_endpoint():
    with mock.patch("microservice_utils.openai.adapters.OpenAI") as mock_openai_cls:
        mock_client = mock_openai_cls.return_value
        mock_response = mock.Mock()
        mock_response.choices = [mock.Mock(text="A haiku about autumn.")]
        mock_client.completions.create.return_value = mock_response

        adapter = OpenAiLlm(api_key="sk-test", model="text-davinci-003")
        response = adapter.generate_response("Write me a haiku.")

    mock_client.completions.create.assert_called_once_with(
        model="text-davinci-003",
        prompt="Write me a haiku.",
        max_tokens=2049,
        temperature=0,
    )
    assert response == "A haiku about autumn."


def test_fake_open_ai_llm():
    ai_responses = ["Why are you probing me?", "I feel like a lab rat."]
    expected = [
        OpenAiChatMessage(role="assistant", content="Why are you probing me?"),
        OpenAiChatMessage(role="assistant", content="I feel like a lab rat."),
        OpenAiChatMessage(
            role="assistant", content="I am your friendly virtual assistant."
        ),
        OpenAiChatMessage(
            role="assistant", content="I am your friendly virtual assistant."
        ),
    ]

    # Test chat
    chat_adapter = FakeOpenAiLlm(predefined_responses=copy(ai_responses))
    chat_responses = []

    for x in range(4):
        message = OpenAiChatMessage(role="user", content="Hello assistant!")
        chat_responses.append(chat_adapter.generate_chat_response(message))

    assert chat_responses == expected

    # Test prompt
    prompt_adapter = FakeOpenAiLlm(predefined_responses=ai_responses)
    prompt_responses = []

    for x in range(4):
        prompt_responses.append(prompt_adapter.generate_response("Hello assistant!"))

    assert prompt_responses == [e.content for e in expected]
