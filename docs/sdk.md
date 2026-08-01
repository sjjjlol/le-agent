# SDK

core 可以脱离 CLI 使用。`FauxProvider` 让单元测试与演示不需要网络或 API key。

```python
import asyncio
from le_agent_ai import FauxProvider, Model, ScriptedResponse
from le_agent_core import Agent, AgentLoopConfig


async def main():
    config = AgentLoopConfig(
        model=Model(provider="faux", id="demo", context_window=100_000, max_output_tokens=1024),
        provider=FauxProvider([ScriptedResponse.text("hello from le-agent")]),
    )
    agent = Agent(config)
    await agent.prompt("say hello")
    print(agent.state.messages[-1].text)


asyncio.run(main())
```

`Agent.subscribe()` 的回调按注册顺序 await；可借此接入自己的 UI 或审计记录。低层用户可直接调用 `agent_loop()` 并消费 `AsyncEventStream`。
