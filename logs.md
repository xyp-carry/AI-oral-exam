## 2026-04-27
对AI口试的Agent系统进行初步设计，完成从基类到子类的系统架构。同时，对接Langchain，实现模型调用。
完成:base_object、base_agent、base_tool模块，测试通过心跳机制

## 2026-04-28
实现全局监控GlobalMonitor，可以控制多个Agent或Tool的启动，后续可通过GlobalMonitor与外界进行通信，用于扩容之类的操作。GlobalMonitor主要通过唯一的异步消息队列与各个Agent和tool进行交互。
完成:GlobalMonitor、base_queue

## 2026-04-29
重构base_tool模块，实现工具的异步调用，以及定义Agent调用工具的方法。更多地，也将GlobalMonitor引入到base_tool模块中，实现工具的实时监控。
完成:base_tool

## 2026-04-30
完成基于MinerU的pdf、docx、图片等文件解析模块。
完成:file_parser

## 2026-05-02
优化base_agent、base_tool模块，为后续配合langraph框架做准备。基于meilisearch实现文件检索功能。
完成：data_tool模块的Insert和Search方法。