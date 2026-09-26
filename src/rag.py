import os
from dataclasses import dataclass
from typing import List
import numpy as np
from openai import OpenAI

# 忽略目录与文件类型
IGNORE_DIRS = {".git", ".venv", "__pycache__", ".idea", ".vscode", "workspace"}
SUPPORTED_EXTS = {".py", ".md", ".json", ".toml", ".txt"}

@dataclass
class CodeChunk:
    file_path: str   # 相对路径，如 "src/tools.py"
    start_line: int  # 起始行号（从 1 开始）
    end_line: int    # 结束行号
    content: str     # 代码正文

    def format_for_display(self) -> str:
        """用于检索展示时，让 Agent 一眼看清文件路径和行号范围"""
        return f"--- {self.file_path} (Lines {self.start_line}-{self.end_line}) ---\n{self.content}"

class CodeChunker:
    """代码库切块器：扫描仓库文件，使用重叠滑动窗口对代码分块"""

    def __init__(
        self,
        repo_path: str = ".",
        chunk_size: int = 40,
        overlap: int = 10,
        supported_exts: set[str] = SUPPORTED_EXTS,
    ):
        self.repo_path = repo_path
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.supported_exts = supported_exts

    def chunk_file(self, rel_path: str) -> List[CodeChunk]:
        """对单个文件进行滑动窗口切块"""
        full_path = os.path.join(self.repo_path, rel_path)
        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
        except Exception:
            return []

        total_lines = len(lines)
        if total_lines == 0:
            return []

        chunks: List[CodeChunk] = []
        step = max(1, self.chunk_size - self.overlap)

        for start_idx in range(0, total_lines, step):
            end_idx = min(start_idx + self.chunk_size, total_lines)
            chunk_content = "".join(lines[start_idx:end_idx])

            chunks.append(
                CodeChunk(
                    file_path=rel_path.replace("\\", "/"),
                    start_line=start_idx + 1,
                    end_line=end_idx,
                    content=chunk_content,
                )
            )

            # 如果已经切到了最后一行，跳出
            if end_idx >= total_lines:
                break

        return chunks

    def chunk_repo(self) -> List[CodeChunk]:
        """扫描整个仓库并完成全部切块"""
        all_chunks: List[CodeChunk] = []

        for root, dirs, files in os.walk(self.repo_path):
            # 过滤不需要的目录
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]

            for file in files:
                if file.startswith("."):
                    continue
                ext = os.path.splitext(file)[1].lower()
                if ext not in self.supported_exts:
                    continue

                full_file_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_file_path, start=self.repo_path)
                file_chunks = self.chunk_file(rel_path)
                all_chunks.extend(file_chunks)

        return all_chunks

class CodebaseRetriever:
    """代码库向量检索器：基于 Ollama Embedding 和余弦相似度内存索引"""

    def __init__(
        self,
        client: OpenAI | None = None,
        model: str = "nomic-embed-text",
        batch_size: int = 32,
    ):
        self.client = client or OpenAI(
            base_url=os.getenv("LLM_BASE_URL", "http://127.0.0.1:11434/v1"),
            api_key=os.getenv("LLM_API_KEY", "ollama"),
        )
        self.model = model
        self.batch_size = batch_size
        self.chunks: List[CodeChunk] = []
        self.vectors: np.ndarray | None = None  # 形状为 (N, 768) 的归一化矩阵

    def _get_embeddings(self, texts: List[str]) -> np.ndarray:
        """调用 Embedding API 获取批量文本向量"""
        # OpenAI SDK 支持一次传入列表批量获取向量
        response = self.client.embeddings.create(
            model=self.model,
            input=texts,
        )
        # 提取向量列表并转为 NumPy 浮点数组
        raw_vectors = [item.embedding for item in response.data]
        return np.array(raw_vectors, dtype=np.float32)

    def build_index(self, chunks: List[CodeChunk]) -> None:
        """为代码块列表构建向量索引"""
        self.chunks = chunks
        if not chunks:
            self.vectors = None
            return

        # 给每个切块附加上文件路径语义
        texts_to_embed = [
            f"File: {c.file_path}\n{c.content}" for c in chunks
        ]

        all_vectors = []
        # 分批处理，防止单次请求载荷过大
        for i in range(0, len(texts_to_embed), self.batch_size):
            batch = texts_to_embed[i : i + self.batch_size]
            batch_vecs = self._get_embeddings(batch)
            all_vectors.append(batch_vecs)

        matrix = np.vstack(all_vectors)

        # 核心：L2 归一化（防止除以 0，加微小常数 1e-9）
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        self.vectors = matrix / np.maximum(norms, 1e-9)

    #召回
    def query(self, query_text: str, top_k: int = 3) -> List[tuple[CodeChunk, float]]:
        """语义检索：返回相似度最高的 top_k 个 (CodeChunk, 相似度得分)"""
        if self.vectors is None or len(self.chunks) == 0:
            return []

        # 1. 获取查询的向量并归一化
        query_vec = self._get_embeddings([query_text])[0]
        q_norm = np.linalg.norm(query_vec)
        if q_norm > 0:
            query_vec = query_vec / q_norm

        # 2. 余弦相似度点积计算：(N, 768) @ (768,) -> (N,)
        similarities = self.vectors @ query_vec

        # 3. 按相似度从高到低排序，截取前 top_k
        top_indices = np.argsort(similarities)[::-1][:top_k]

        results = []
        for idx in top_indices:
            results.append((self.chunks[idx], float(similarities[idx])))

        return results
#
# if __name__ == "__main__":
#     from dotenv import load_dotenv
#     load_dotenv()
#
#     print("1. 正在扫描并切块仓库...")
#     chunker = CodeChunker(repo_path=".")
#     chunks = chunker.chunk_repo()
#     print(f"切出 {len(chunks)} 个代码块。")
#
#     print("\n2. 正在构建向量索引 (调用 nomic-embed-text)...")
#     retriever = CodebaseRetriever()
#     retriever.build_index(chunks)
#     print("向量索引构建完毕！")
#
#     # 3. 模糊语义检索测试：不包含任何函数名（read_file），看能否精准定位！
#     test_query = "如何安全地打开并读取文件内容"
#     print(f"\n3. 执行语义检索: '{test_query}'")
#     matches = retriever.query(test_query, top_k=2)
#
#     for rank, (chunk, score) in enumerate(matches, 1):
#         print(f"\n[排名 {rank} | 相似度得分: {score:.4f}]")
#         print(chunk.format_for_display())