import asyncio
import tempfile
from pathlib import Path
import shutil


def test_memory_add_fact_dedup():
    """Test that similar facts are deduplicated."""
    from core.memory import MemorySystem
    import uuid
    
    # Use a unique test directory
    unique_id = str(uuid.uuid4())[:8]
    test_dir = Path(f"data/test_memory_dedup_{unique_id}")
    test_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # Mock settings
        from core import config
        original_vault = config.settings.memory.vault_path
        config.settings.memory.vault_path = test_dir
        
        memory = MemorySystem()
        
        # Add a fact with unique content
        fact1 = f"Le gusta la música de RAMMA {unique_id}"
        result1 = asyncio.run(memory.add_fact(
            fact1,
            source="test",
            confidence=1.0
        ))
        assert result1 is True
        
        # Get count before
        count_before = memory.semantic_collection.count()
        
        # Add a very similar fact (should be deduplicated)
        result2 = asyncio.run(memory.add_fact(
            fact1,  # Same fact
            source="test2",
            confidence=0.9
        ))
        assert result2 is False  # Should not add duplicate
        
        # Count should not increase
        count_after = memory.semantic_collection.count()
        assert count_after == count_before
        
        # Add a different fact
        fact2 = f"Le gusta Paulo Londra {unique_id}"
        result3 = asyncio.run(memory.add_fact(
            fact2,
            source="test",
            confidence=1.0
        ))
        assert result3 is True
        
        # Check that count increased by 1
        count_final = memory.semantic_collection.count()
        assert count_final == count_before + 1
        
        config.settings.memory.vault_path = original_vault
    finally:
        # Clean up
        if test_dir.exists():
            try:
                shutil.rmtree(test_dir, ignore_errors=True)
            except:
                pass


def test_memory_procedural():
    """Test procedural memory for action sequences."""
    from core.memory import MemorySystem
    
    test_dir = Path("data/test_memory_procedural")
    test_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        from core import config
        original_vault = config.settings.memory.vault_path
        config.settings.memory.vault_path = test_dir
        
        memory = MemorySystem()
        
        # Add a procedure
        result = asyncio.run(memory.add_procedural(
            action="play music",
            context="user asked for music",
            result="Playing RAMMA",
            success=True
        ))
        assert result is True
        
        # Get similar procedures
        procedures = asyncio.run(memory.get_similar_procedures(
            context="user wants music",
            limit=3
        ))
        assert len(procedures) > 0
        
        config.settings.memory.vault_path = original_vault
    finally:
        if test_dir.exists():
            try:
                shutil.rmtree(test_dir, ignore_errors=True)
            except:
                pass


def test_memory_episodic_context():
    """Test getting relevant episodic context."""
    from core.memory import MemorySystem
    
    test_dir = Path("data/test_memory_episodic")
    test_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        from core import config
        original_vault = config.settings.memory.vault_path
        config.settings.memory.vault_path = test_dir
        
        memory = MemorySystem()
        
        # Add a conversation
        asyncio.run(memory.add_conversation(
            user_input="Poné música de RAMMA",
            response="Ok, poniendo música de RAMMA",
            intent="music_search",
            metadata={"test": True}
        ))
        
        # Get relevant context
        context = asyncio.run(memory.get_relevant_context(
            query="música",
            limit=3
        ))
        assert len(context) > 0
        
        config.settings.memory.vault_path = original_vault
    finally:
        if test_dir.exists():
            try:
                shutil.rmtree(test_dir, ignore_errors=True)
            except:
                pass


if __name__ == "__main__":
    test_memory_add_fact_dedup()
    test_memory_procedural()
    test_memory_episodic_context()
    print("All tests passed!")
