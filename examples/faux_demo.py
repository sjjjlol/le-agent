"""Run with: uv run python examples/faux_demo.py"""

import asyncio

from le_agent_ai import FauxProvider, Model, ScriptedResponse
from le_agent_core import Agent, AgentLoopConfig


async def main() -> None:
    config = AgentLoopConfig(
        model=Model(provider="faux", id="demo", context_window=100_000, max_output_tokens=1024),
        provider=FauxProvider([ScriptedResponse.text("hello from le-agent")]),
    )
    agent = Agent(config)
    await agent.prompt("say hello")
    print(agent.state.messages[-1].text)


asyncio.run(main())
