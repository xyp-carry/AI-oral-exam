from AIOralExamSystem.Tool.base_tool import BaseTool
from langchain_core.tools import tool
from functools import wraps
from pydantic import BaseModel, Field

class RAGToolInput(BaseModel):
    # query: str = Field(description="要查询的文本")
    weight_kg: float = Field(description="体重(千克)")
    height_m: float = Field(description="身高(米)")
    
RAGDescription = """计算用户的BMI指数。当用户询问体重指数、BMI时使用。参数：体重(千克)，身高(米)"""

class RAGTool(BaseTool):
    """RAG工具"""
    def __init__(self, name: str):
        super().__init__(name)
        # self.description = "这是一个rag工具，其中存放了同学上传的文本资料或者老师存放的文本资料。"
        self.description = """计算用户的BMI指数。当用户询问体重指数、BMI时使用。参数：体重(千克)，身高(米)"""
    async def _run(self, weight_kg: float, height_m: float) -> str:
        bmi = weight_kg / (height_m ** 2)
        print(f"您的BMI值为 {bmi:.1f}。")
        return f"您的BMI值为 {bmi:.1f}。"

    def get_description(self) -> str:
        return self.description
   
