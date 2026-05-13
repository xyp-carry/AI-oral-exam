from AIOralExamSystem.Tool.base_tool import BaseTool
from AIOralExamSystem.Tool.rag.file_tool import FileParserTool

from langchain_core.tools import tool
from functools import wraps
from pydantic import BaseModel, Field
from meilisearch import Client
import json
import uuid
import re
import asyncio
from concurrent.futures import ThreadPoolExecutor



class SearchToolInput(BaseModel):
    query: str = Field(description="用于查询信息的一段话")
    
SearchDescription = """用一句话形容你所需要的信息，并调用该API查询相关信息"""

class SearchTool(BaseTool):
    """搜索工具"""
    def __init__(self, name: str):
        super().__init__(name)
        # self.description = "这是一个rag工具，其中存放了同学上传的文本资料或者老师存放的文本资料。"
        self.description = """用一句话形容你所需要的信息，并调用该API查询相关信息"""
        self.client = Client("http://localhost:7700")
    

    async def _run(self, query: str, source: str) -> str:
        """
        query: 用户查询的信息
        source: 用户的id，用于指定查询的文档来源
        """
        
        with ThreadPoolExecutor(max_workers=1) as executor:
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                executor,
                self.search,
                query,
                source
            )
        
        
        
        return f"您查询的信息是：{json.dumps(results["hits"][:3])}"

    def search(self, query: str, source: str):
        index = self.client.index("test")
        results = index.search(query, {
            "filter": f'source = "{source}"',
            "hybrid": {
                "embedder": "default",
                "semanticRatio": 0.7  # 70%语义匹配 + 30%关键词匹配
            }
        })
        return results

    def get_description(self) -> str:
        return self.description

class InsertTool(BaseTool):
    """插入工具"""
    def __init__(self, name: str, token: str):
        super().__init__(name)
        self.description = """将文档插入到索引中"""
        self.client = Client("http://localhost:7700")
        self.fileParser = FileParserTool(token, "file_parser")

    
    async def _run(self, data: list | str, source: str, type:str = "file") -> str:
        if type == "file":
            chunksList = await self.fileParser.execute(file_paths=data)
        else:
            chunksList = data

        if not chunksList:
            return "没有可插入的文档"

        index = self.client.index("test")

        settings = {
            "embedders": {
                "default": {
                    "source": "rest",
                    "url": "https://open.bigmodel.cn/api/paas/v4/embeddings",
                    "dimensions": 2048,
                    "documentTemplate": "{{doc.content}}",
                    "request": {
                        "model": "embedding-3",
                        "input": ["{{text}}"]
                    },
                    "response": {
                        "data": [
                            {
                                "embedding": "{{embedding}}"
                            }
                        ]
                    },
                    "headers": {
                        "Authorization": "Bearer f3243c87fe4547698d5beee226f71452.3UGVyBevympxI2TA"
                    }
                }
            },
            "filterableAttributes": ["source"]
        }

        
        task = index.update_settings(settings)
        self.client.wait_for_task(task.task_uid)
        for chunks in chunksList:
            documents = [
                {
                    "id": str(uuid.uuid4()),
                    "source": source,
                    "content": chunk
                }
                for chunk in chunks if self.is_meaningful_text(chunk)
            ]
            task = index.add_documents(documents)
            self.client.wait_for_task(task.task_uid)

        
        return f"成功插入 {len(documents)} 条文档"
    def get_description(self) -> str:
        return self.description
    
    def is_meaningful_text(self, text: str) -> bool:
        """
        判断文本是否有意义，返回 True 表示保留，False 表示丢弃
        """
        if not text or not text.strip():
            return False
        
        # 去除首尾空白后获取纯文本
        clean_text = text.strip()
        
        # 1. 长度过短过滤（比如少于 5 个字符，通常是残留的标题、标点或乱码）
        if len(clean_text) < 5:
            return False
        
        # 2. 纯数字、纯字母、纯符号过滤（没有实际语义）
        # \w 匹配字母、数字、下划线。如果去除它们后什么都不剩，说明没有中文或实质性词汇
        if not re.sub(r'[\w\s\.,;:!?\-\'\"()（）：。，；！？、]', '', clean_text):
            return False
            
        # 3. 纯标点符号、空白字符、特殊字符过滤
        if re.match(r'^[\s\W_]+$', clean_text):
            return False
            
        # 4. 常见无意义模板/页眉页脚过滤（根据你的具体文档类型定制）
        # 例如："第 X 页"、"Copyright © 2023"、"目录"、"Table of Contents"
        useless_patterns = [
            r'^第\s*\d+\s*页$', 
            r'^\d+\s*/\s*\d+$',  # 比如 "1 / 10"
            r'^(目录|目录\n|TABLE OF CONTENTS)$',
            r'^(版权所有|Copyright|All rights reserved).*',
            r'^\.{3,}$',         # 纯省略号 "......"
            r'^-+$',             # 纯横线 "----------"
            r'^=+$'              # 纯等号 "=========="
        ]
        for pattern in useless_patterns:
            if re.match(pattern, clean_text, re.IGNORECASE):
                return False
                
        # 5. 有效信息密度过滤（中文字符占比太低，说明可能是满篇英文乱码夹杂一个中文字，或者满篇特殊符号）
        # 统计中文字符数量
        chinese_chars = re.findall(r'[\u4e00-\u9fff]', clean_text)
        # 如果文本较长（>20字），但中文字符少于 20%，大概率是无意义的乱码或公式排版残留
        if len(clean_text) > 20 and len(chinese_chars) / len(clean_text) < 0.2:
            # 如果你处理的是纯英文文档，请注释掉这一段！
            return False

        return True
