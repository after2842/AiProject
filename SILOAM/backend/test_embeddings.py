#!/usr/bin/env python3
"""
Test script to verify embedding generation works correctly.
"""
import os
from openai import OpenAI

def test_embedding_generation():
    """Test OpenAI embedding generation"""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("❌ OPENAI_API_KEY environment variable not set")
        return False
    
    try:
        client = OpenAI(api_key=api_key)
        
        # Test with sample text
        test_text = "This is a test product description for embedding generation."
        
        print(f"🧪 Testing embedding generation for: '{test_text}'")
        
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input=test_text
        )
        
        embedding = response.data[0].embedding
        
        print(f"✅ Embedding generated successfully!")
        print(f"📊 Embedding dimensions: {len(embedding)}")
        print(f"🔢 First 5 values: {embedding[:5]}")
        
        # Verify it's the expected dimension for text-embedding-3-small
        expected_dims = 1536
        if len(embedding) == expected_dims:
            print(f"✅ Embedding dimensions match expected ({expected_dims})")
            return True
        else:
            print(f"❌ Expected {expected_dims} dimensions, got {len(embedding)}")
            return False
            
    except Exception as e:
        print(f"❌ Error generating embedding: {e}")
        return False

if __name__ == "__main__":
    print("🚀 Testing OpenAI Embedding Generation")
    print("=" * 50)
    
    success = test_embedding_generation()
    
    if success:
        print("\n🎉 All tests passed! Embedding generation is working correctly.")
    else:
        print("\n💥 Tests failed. Please check your OpenAI API key and configuration.")
