## 二、文件统计
- 总文件数：[FIELD:total_files]
- 代码文件数：[FIELD:code_files]
- 开发文档与报告数：[FIELD:doc_files]
- 其他文件数：[FIELD:other_files]
- 网盘/外部文档标注：[FIELD:external_doc_flag | 备注：如存在初赛文档存于网盘，填"⚠️ 评委需核查外部网盘文档：{链接/说明}"，否则填"无"]

--ps--:总文件要从项目仓库中统计，包括所有子目录。
代码文件数要根据文件扩展名统计，包括.c、.h、.c、.h、.py、.rs、.sh、Makefile、Kconfig等。
开发文档与报告数要根据文件扩展名统计，包括Markdown文档、文本文件等。
其他文件数要根据文件扩展名统计，包括JSON数据文件、QASM量子线路文件、PNG图片、PPTX演示文稿、Git示例钩子、Git内部文件、gitignore、gitkeep等。
网盘之类的文档需要通过检索的方式进行统计，包括在项目仓库中引用的网盘文档。