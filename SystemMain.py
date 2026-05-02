from AIOralExamSystem.Agent.base_Agent import BaseAgent
from langchain_core.tools import tool

from config import get_settings

from AIOralExamSystem.utils.monitor import GlobalMonitor
import asyncio
from loguru import logger

from AIOralExamSystem.Tool.rag.file_tool import FileParserTool
from AIOralExamSystem.Tool.rag.data_tool import SearchToolInput, SearchTool, SearchDescription

class HealthAgent(BaseAgent):
    """健康助手子类"""
    
    def __init__(self, model_settings: dict, source: str):
        # 只需要传 llm 和 prompt，工具注册全自动完成
        super().__init__("RAG助手", model_settings)
        self.source = source

    def get_tools(self):
        """获取工具"""
        @tool(args_schema=SearchToolInput, description=SearchDescription)
        async def search(query: str) -> str:
            tool = SearchTool("search_tool")
            response = await tool.execute(query, self.source)
            return response
        return [search] 

    async def execute(self, **kwargs):
        logger.info(f"开始执行 {self.name}。")
        self.set_heartbeat_interval(1)
        await self.start_heartbeat()
        response = await self.agent.ainvoke({
            "messages": [{"role": "user", "content": "我想知道他的研究背景，帮我从数据库里面看看，他的名称我已经提前注入了，你只需要调用search工具即可"}]
        })
        print(response['messages'][-1].content)
        await self.stop_heartbeat()

settings = get_settings()

# monitor = GlobalMonitor()
async def main():
    monitor = GlobalMonitor()
    await monitor.start()          
    # _monitor_loop 在同一个循环中运行
    tasks = []
    for i in range(1):
        agent = HealthAgent(settings.model_dump(mode="json"), "10693")
        tasks.append(asyncio.create_task(agent.run()))

    await asyncio.gather(*tasks)

asyncio.run(main())
# async def filetest():
#     monitor = GlobalMonitor()
#     await monitor.start()    
#     file_tool = InsertTool("insert_tool",settings.mineru_api_key)
#     await file_tool.execute(data=["./File/opening_report.pdf"], source="10693", type="file")
#     print("解析完成")

# asyncio.run(filetest())

