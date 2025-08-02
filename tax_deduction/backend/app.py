from transformers import AutoTokenizer, BitsAndBytesConfig, Gemma3ForCausalLM
import torch
from flask import Flask, request, jsonify
from flask_cors import CORS
import json

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Global variables for model and tokenizer
model = None
tokenizer = None
device = None

def load_model():
    """Load the model and tokenizer once when server starts"""
    global model, tokenizer, device
    
    print("Loading model and tokenizer...")
    
    model_id = "google/gemma-3-1b-it"
    
    # Check if MPS performance shader for apple silicon available
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    print(f"Using device: {device}")
    
    # Load model and tokenizer
    model = Gemma3ForCausalLM.from_pretrained(
        model_id, 
        # quantization_config=quantization_config
    ).to(device).eval()
    
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    
    print("Model and tokenizer loaded successfully!")

# Load model when server starts
load_model()

def get_response(query):
    """Generate response using pre-loaded model"""
    global model, tokenizer, device
    
    if model is None or tokenizer is None:
        return "Error: Model not loaded"
    
    try:
        messages = [
            {
                "role": "system",
                "content": [{"type": "text", "text": "You are a helpful assistant."},]
            },
            {
                "role": "user",
                "content": [{"type": "text", "text": f"{query}"},]
            },
        ]
        
        inputs = tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        ).to(device)

        with torch.inference_mode():
            outputs = model.generate(**inputs, max_new_tokens=256)

        outputs = tokenizer.batch_decode(outputs)
        print(outputs[0])

        # Extract only the model's response
        response = outputs[0]

        # Find the model's answer after <start_of_turn>model
        if "<start_of_turn>model" in response:
            model_answer = response.split("<start_of_turn>model")[1].split("<end_of_turn>")[0].strip()
        else:
            model_answer = "I couldn't generate a proper response."

        print(f"Model answer: {model_answer}")
        return model_answer
        
    except Exception as e:
        print(f"Error generating response: {e}")
        return f"Error: {str(e)}"




@app.route('/api/chat', methods=['POST'])
def chat():
    try:
        data = request.get_json()
        user_message = data.get('message', '')
        ai_response = get_response(user_message)

        
        return jsonify({
            'success': True,
            'response': ai_response,
            'message': user_message
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'healthy',
        'message': 'Flask API is running'
    })

if __name__ == '__main__':
    app.run(debug=True, port=5000) 