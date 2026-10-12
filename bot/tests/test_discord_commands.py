"""Comandos /x9 chamados direto pelo callback (sem conectar no Discord)."""
from types import SimpleNamespace

import pytest

pytest.importorskip("discord")

from conftest import ADMIN_DISCORD  # noqa: E402


class Resp:
    def __init__(self):
        self.sent = []

    async def send_message(self, content=None, **kw):
        self.sent.append((content, kw))


def inter(manage_guild):
    return SimpleNamespace(user=SimpleNamespace(id=1, guild_permissions=SimpleNamespace(
        manage_guild=manage_guild)), response=Resp())


@pytest.fixture()
async def bot(backend):
    from x9.brain import Brain
    from x9.discord_bot import X9Bot
    from x9.stt import Transcriber
    b = X9Bot(Brain(SimpleNamespace(name="p"), backend.botapi), backend, Transcriber())
    yield b
    await b.close()


def cmd(bot, name):
    return bot.tree.get_command("x9").get_command(name).callback


async def test_vincular_exige_gerenciar_servidor(bot, backend):
    membro = SimpleNamespace(id=333, mention="<@333>")
    i = inter(False)
    await cmd(bot, "vincular")(i, membro, "Amigo#0002")
    assert "Gerenciar Servidor" in i.response.sent[0][0]
    assert "333" not in backend.db.discord_links()

    i = inter(True)
    await cmd(bot, "vincular")(i, membro, "Amigo#0002")
    assert "**amigo**" in i.response.sent[0][0]
    assert backend.db.discord_links()["333"] == backend.botapi._Ctx().resolve("amigo")

    i = inter(True)
    await cmd(bot, "vincular")(i, membro, "Ninguem#000")
    assert "Não achei" in i.response.sent[0][0] and i.response.sent[0][1]["ephemeral"]


async def test_desvincular_exige_gerenciar_servidor(bot, backend):
    membro = SimpleNamespace(id=int(ADMIN_DISCORD))
    i = inter(False)
    await cmd(bot, "desvincular")(i, membro)
    assert ADMIN_DISCORD in backend.db.discord_links()
    await cmd(bot, "desvincular")(inter(True), membro)
    assert ADMIN_DISCORD not in backend.db.discord_links()
