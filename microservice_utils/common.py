import asyncio


def get_event_loop():
    # get currently running loop if one exists
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    # if one is already running then return that one
    if current_loop is not None:
        return current_loop

    # create new event loop
    policy = asyncio.get_event_loop_policy()
    try:
        return policy.get_event_loop()
    except RuntimeError:
        policy.set_event_loop(loop=policy.new_event_loop())
        return policy.get_event_loop()
