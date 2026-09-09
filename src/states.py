from aiogram.fsm.state import State, StatesGroup


class Post(StatesGroup):
    title = State()
    body = State()
    image = State()
    confirm = State()
