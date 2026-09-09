from aiogram.fsm.state import State, StatesGroup


class Post(StatesGroup):
    title = State()
    body = State()
    image = State()
    publish = State()
    schedule_date = State()
    schedule_time = State()
