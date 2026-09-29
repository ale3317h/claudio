from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import chromadb
import structlog
from chromadb.config import Settings as ChromaSettings
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from core.config import settings

logger = structlog.get_logger(__name__)

EXCLUDED_DIRS = {".obsidian", ".git", ".venv", ".pytest_cache", "__pycache__", "data", "node_modules"}


class VaultIndexer(FileSystemEventHandler):
    def __init__(self, memory_system: "MemorySystem"):
        self.memory = memory_system
        self.vault_path = settings.memory.vault_path

    def on_modified(self, event):
        if event.is_directory:
            return
        if event.src_path.endswith(".md"):
            path = Path(event.src_path)
            if not (EXCLUDED_DIRS & set(path.parts)):
                self.memory.index_file(path)

    def on_created(self, event):
        self.on_modified(event)

    def on_deleted(self, event):
        if event.is_directory:
            return
        if event.src_path.endswith(".md"):
            self.memory.delete_document(event.src_path)


class MemorySystem:
    def __init__(self):
        self.vault_path = settings.memory.vault_path
        self.embeddings = OllamaEmbeddings(model=settings.memory.embedding_model)

        self.client = chromadb.PersistentClient(
            path=str(self.vault_path / "data" / "chromadb"),
            settings=ChromaSettings(anonymized_telemetry=False),
        )

        self.semantic_collection = self.client.get_or_create_collection(
            name="vault_semantic",
            metadata={"hnsw:space": "cosine"},
        )
        self.episodic_collection = self.client.get_or_create_collection(
            name="vault_episodic",
            metadata={"hnsw:space": "cosine"},
        )
        self.procedural_collection = self.client.get_or_create_collection(
            name="vault_procedural",
            metadata={"hnsw:space": "cosine"},
        )

        self.splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[
                ("#", "h1"),
                ("##", "h2"),
                ("###", "h3"),
            ]
        )
        self.recursive_splitter = RecursiveCharacterTextSplitter(
            chunk_size=500, chunk_overlap=50
        )

        self.observer: Optional[Observer] = None

    def start_watching(self):
        self.observer = Observer()
        self.observer.schedule(VaultIndexer(self), str(self.vault_path), recursive=True)
        self.observer.start()
        logger.info("vault_watcher_started", path=str(self.vault_path))

    def stop_watching(self):
        if self.observer:
            self.observer.stop()
            self.observer.join()
            logger.info("vault_watcher_stopped")

    def index_file(self, file_path: Path, force: bool = False) -> None:
        try:
            relative_path = file_path.relative_to(self.vault_path)
            mtime = datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()

            if not force:
                existing = self.semantic_collection.get(
                    where={"source": str(relative_path)},
                    limit=1,
                    include=["metadatas"],
                )
                if (
                    existing["ids"]
                    and existing["metadatas"]
                    and existing["metadatas"][0].get("modified") == mtime
                ):
                    logger.debug("file_unchanged_skip", file=str(relative_path))
                    return

            content = file_path.read_text(encoding="utf-8")

            self.delete_document(str(relative_path))

            docs = self.splitter.split_text(content)
            texts = []
            for doc in docs:
                if len(doc.page_content) > 600:
                    for piece in self.recursive_splitter.split_text(doc.page_content):
                        texts.append((piece, doc.metadata))
                else:
                    texts.append((doc.page_content, doc.metadata))
            for i, (text, meta) in enumerate(texts):
                chunk_id = f"{relative_path}#{i}"
                metadata = {
                    "source": str(relative_path),
                    "chunk_index": i,
                    "header": meta.get("h1", ""),
                    "subheader": meta.get("h2", ""),
                    "modified": mtime,
                }
                embedding = self.embeddings.embed_query(text)
                self.semantic_collection.add(
                    ids=[chunk_id],
                    embeddings=[embedding],
                    documents=[text],
                    metadatas=[metadata],
                )

            logger.debug("file_indexed", file=str(relative_path), chunks=len(texts))
        except Exception as e:
            logger.error("index_file_failed", file=str(file_path), error=str(e))

    def delete_document(self, source: str) -> None:
        try:
            results = self.semantic_collection.get(where={"source": source})
            if results["ids"]:
                self.semantic_collection.delete(ids=results["ids"])
                logger.debug("document_deleted", source=source, count=len(results["ids"]))
        except Exception as e:
            logger.error("delete_document_failed", source=source, error=str(e))

    def reindex_vault(self, full: bool = False) -> None:
        if full:
            self.client.delete_collection("vault_semantic")
            self.semantic_collection = self.client.get_or_create_collection(
                name="vault_semantic",
                metadata={"hnsw:space": "cosine"},
            )

        md_files = list(self.vault_path.rglob("*.md"))
        logger.info("reindexing_vault", files=len(md_files))

        for file_path in md_files:
            if not (EXCLUDED_DIRS & set(file_path.parts)):
                self.index_file(file_path)

        logger.info("vault_reindex_complete")

    async def search(
        self,
        query: str,
        top_k: int = 5,
        collection: str = "semantic",
        where: Optional[Dict] = None,
    ) -> List[Dict[str, Any]]:
        coll = getattr(self, f"{collection}_collection")
        query_embedding = self.embeddings.embed_query(query)

        results = coll.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        memories = []
        for i in range(len(results["ids"][0])):
            memories.append({
                "content": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "distance": results["distances"][0][i],
                "score": 1 - results["distances"][0][i],
            })

        return memories

    async def add_conversation(
        self,
        user_input: str,
        response: str,
        intent: str,
        metadata: Dict[str, Any],
    ) -> None:
        timestamp = datetime.now().isoformat()
        doc_id = f"conv_{timestamp}_{hash(user_input) % 10000}"

        content = f"User: {user_input}\nCLAUDIO: {response}"
        meta = {
            "type": "conversation",
            "intent": intent,
            "timestamp": timestamp,
            **(metadata or {}),
        }
        meta = {k: v for k, v in meta.items() if isinstance(v, (str, int, float, bool)) or v is None}

        embedding = self.embeddings.embed_query(content)
        self.episodic_collection.add(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[content],
            metadatas=[meta],
        )

    async def add_procedural(self, action: str, context: str, result: str, success: bool = True) -> bool:
        """Almacena un procedimiento/acción exitosa para aprendizaje."""
        try:
            content = f"Contexto: {context}\nAcción: {action}\nResultado: {result}"
            embedding = self.embeddings.embed_query(content)
            
            doc_id = f"proc_{hash(action) % 100000}_{int(datetime.now().timestamp())}"
            self.procedural_collection.add(
                ids=[doc_id],
                embeddings=[embedding],
                documents=[content],
                metadatas=[{
                    "type": "procedural",
                    "action": action[:200],
                    "context": context[:200],
                    "success": success,
                    "timestamp": datetime.now().isoformat(),
                }],
            )
            logger.debug("procedural_added", action=action[:50], success=success)
            return True
        except Exception as e:
            logger.warning("add_procedural_failed", error=str(e)[:100])
            return False
    
    async def get_similar_procedures(self, context: str, limit: int = 3) -> List[Dict[str, Any]]:
        """Busca procedimientos similares para aprender de experiencias pasadas."""
        try:
            query_embedding = self.embeddings.embed_query(context)
            results = self.procedural_collection.query(
                query_embeddings=[query_embedding],
                n_results=limit,
                include=["documents", "metadatas", "distances"],
            )
            
            procedures = []
            for i in range(len(results["ids"][0])):
                distance = results["distances"][0][i]
                if distance < 0.4:
                    procedures.append({
                        "content": results["documents"][0][i],
                        "metadata": results["metadatas"][0][i],
                        "similarity": 1 - distance,
                    })
            return procedures
        except Exception as e:
            logger.warning("get_similar_procedures_failed", error=str(e)[:100])
            return []
        """Almacena un hecho en semantic memory con deduplicación semántica."""
        fact = fact.strip()
        if len(fact) < 10:
            return False
        
        # Deduplicación: buscar hechos similares (cosine similarity > 0.85)
        try:
            embedding = self.embeddings.embed_query(fact)
            existing = self.semantic_collection.query(
                query_embeddings=[embedding],
                n_results=3,
                include=["documents", "metadatas", "distances"],
            )
            
            # Si hay un hecho muy similar (distancia < 0.15 = similarity > 0.85), no duplicar
            for i, dist in enumerate(existing["distances"][0] if existing["distances"] else []):
                if dist < 0.15:
                    existing_fact = existing["documents"][0][i]
                    existing_meta = existing["metadatas"][0][i]
                    # Actualizar timestamp si es el mismo hecho
                    if existing_meta.get("type") == "fact":
                        self.semantic_collection.update(
                            ids=[existing["ids"][0][i]],
                            metadatas=[{**existing_meta, "updated": datetime.now().isoformat()}],
                        )
                        logger.debug("fact_updated", fact=fact[:50], existing=existing_fact[:50])
                        return False
        except Exception as e:
            logger.warning("fact_dedup_failed", error=str(e)[:100])
        
        # Almacenar nuevo hecho
        doc_id = f"fact_{hash(fact) % 100000}_{int(datetime.now().timestamp())}"
        self.semantic_collection.add(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[fact],
            metadatas=[{
                "type": "fact",
                "source": source,
                "confidence": confidence,
                "timestamp": datetime.now().isoformat(),
                "updated": datetime.now().isoformat(),
            }],
        )
        logger.debug("fact_added", fact=fact[:50], source=source)
        return True

    def append_memory_fact(self, fact: str, path=None) -> bool:
        fact = " ".join(str(fact or "").strip().lstrip("-•* ").split())
        if len(fact) < 10:
            return False
        from datetime import date
        target = Path(path) if path else (self.vault_path / "memoria.md")
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        if not existing:
            existing = "# Memoria de CLAUDIO\n\nHechos duraderos sobre Señor (auto-gestionado, editable).\n\n"
        if fact.lower() in existing.lower():
            return False
        with open(target, "a", encoding="utf-8") as f:
            f.write(f"- {fact} ({date.today().isoformat()})\n")
        return True

    async def get_recent_conversations(self, limit: int = 10) -> List[Dict[str, Any]]:
        results = self.episodic_collection.get(
            limit=limit,
            include=["documents", "metadatas"],
        )
        conversations = []
        for i in range(len(results["ids"])):
            conversations.append({
                "content": results["documents"][i],
                "metadata": results["metadatas"][i],
            })
        return sorted(conversations, key=lambda x: x["metadata"]["timestamp"], reverse=True)
    
    async def get_relevant_context(self, query: str, limit: int = 3) -> List[Dict[str, Any]]:
        """Busca conversaciones relevantes para dar contexto al LLM."""
        try:
            query_embedding = self.embeddings.embed_query(query)
            results = self.episodic_collection.query(
                query_embeddings=[query_embedding],
                n_results=limit,
                include=["documents", "metadatas", "distances"],
            )
            
            context = []
            for i in range(len(results["ids"][0])):
                distance = results["distances"][0][i]
                # Solo incluir si es relativamente relevante (distancia < 0.5)
                if distance < 0.5:
                    context.append({
                        "content": results["documents"][0][i],
                        "metadata": results["metadatas"][0][i],
                        "relevance": 1 - distance,
                    })
            return context
        except Exception as e:
            logger.warning("get_relevant_context_failed", error=str(e)[:100])
            return []
    
    async def summarize_old_conversations(self, days_old: int = 7) -> Optional[str]:
        """Resume conversaciones antiguas para contexto a largo plazo."""
        try:
            from datetime import timedelta
            cutoff = (datetime.now() - timedelta(days=days_old)).isoformat()
            
            # Obtener conversaciones antiguas
            results = self.episodic_collection.get(
                where={"timestamp": {"$lt": cutoff}},
                limit=20,
                include=["documents", "metadatas"],
            )
            
            if not results["ids"]:
                return None
            
            # Agrupar por tema/intent
            conversations = []
            for i in range(len(results["ids"])):
                conversations.append({
                    "content": results["documents"][i],
                    "intent": results["metadatas"][i].get("intent", "unknown"),
                    "timestamp": results["metadatas"][i].get("timestamp", ""),
                })
            
            # Crear resumen
            summary_parts = []
            intents = {}
            for conv in conversations:
                intent = conv["intent"]
                if intent not in intents:
                    intents[intent] = []
                intents[intent].append(conv["content"][:100])
            
            for intent, convs in intents.items():
                summary_parts.append(f"- {intent}: {len(convs)} conversaciones")
            
            return "\n".join(summary_parts) if summary_parts else None
            
        except Exception as e:
            logger.warning("summarize_old_conversations_failed", error=str(e)[:100])
            return None
    
    async def add_fact(self, fact: str, source: str, confidence: float = 1.0) -> bool:
        """Almacena un hecho en semantic memory con deduplicación semántica."""
        fact = fact.strip()
        if len(fact) < 10:
            return False
        
        # Deduplicación: buscar hechos similares (cosine similarity > 0.85)
        try:
            embedding = self.embeddings.embed_query(fact)
            existing = self.semantic_collection.query(
                query_embeddings=[embedding],
                n_results=3,
                include=["documents", "metadatas", "distances"],
            )
            
            # Si hay un hecho muy similar (distancia < 0.15 = similarity > 0.85), no duplicar
            for i, dist in enumerate(existing["distances"][0] if existing["distances"] else []):
                if dist < 0.15:
                    existing_fact = existing["documents"][0][i]
                    existing_meta = existing["metadatas"][0][i]
                    # Actualizar timestamp si es el mismo hecho
                    if existing_meta.get("type") == "fact":
                        self.semantic_collection.update(
                            ids=[existing["ids"][0][i]],
                            metadatas=[{**existing_meta, "updated": datetime.now().isoformat()}],
                        )
                        logger.debug("fact_updated", fact=fact[:50], existing=existing_fact[:50])
                        return False
        except Exception as e:
            logger.warning("fact_dedup_failed", error=str(e)[:100])
        
        # Almacenar nuevo hecho
        doc_id = f"fact_{hash(fact) % 100000}_{int(datetime.now().timestamp())}"
        self.semantic_collection.add(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[fact],
            metadatas=[{
                "type": "fact",
                "source": source,
                "confidence": confidence,
                "timestamp": datetime.now().isoformat(),
                "updated": datetime.now().isoformat(),
            }],
        )
        logger.debug("fact_added", fact=fact[:50], source=source)
        return True


memory_system = MemorySystem()