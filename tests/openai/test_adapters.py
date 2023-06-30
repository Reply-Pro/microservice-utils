from copy import copy

from microservice_utils.openai.adapters import (
    FakeOpenAiLlm,
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
