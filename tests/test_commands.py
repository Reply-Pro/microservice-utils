from freezegun import freeze_time
from microservice_utils.commands import Command, CommandEnvelope
from pydantic import BaseModel


class User(BaseModel):
    email: str


class AddNewAccount(Command):
    title: str
    admin: User


@freeze_time("2023-08-02 19:10+00:00")
def test_command_envelope_to_publishable_json():
    command = AddNewAccount(
        title="New Pecan Ventures", admin=User(email="random@test.com")
    )

    message = CommandEnvelope.create(command)

    assert message.command == "AddNewAccount"
    assert message.timestamp == 1691003400
    assert message.parameters == command

    publishable = message.to_publishable_json()

    assert isinstance(publishable, bytes)
    assert command.json().encode("utf-8") in publishable


@freeze_time("2023-08-02 19:10+00:00")
def test_command_envelope_from_published_json():
    raw_message = b"""
    {"command": "AddNewAccount", "parameters": {"title": "Walnut Marine",
    "admin": {"email": "random@test.com"}}, "timestamp": 1691003400}
    """

    message = CommandEnvelope.from_published_json(raw_message)

    assert message.command == "AddNewAccount"
    assert message.parameters == {
        "title": "Walnut Marine",
        "admin": {"email": "random@test.com"},
    }
    assert message.timestamp == 1691003400
