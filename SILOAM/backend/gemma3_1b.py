from transformers import AutoTokenizer, BitsAndBytesConfig, Gemma3ForCausalLM
import torch
import pandas as pd
import json

model_id = "google/gemma-3-1b-it"

query = ""
# Check if MPS performace shader for apple silicon available
device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
print(f"Using device: {device}")

# quantization_config = BitsAndBytesConfig(load_in_4bit=True)

model = Gemma3ForCausalLM.from_pretrained(
    model_id, 
    # quantization_config=quantization_config
).to(device).eval()

tokenizer = AutoTokenizer.from_pretrained(model_id)
messages = []

def get_response(query):
    new_messages = [
            {
                "role": "system",
                "content": [{"type": "text", "text": "You are a helpful assistant."},]
            },
            {
                "role": "user",
                "content": [{"type": "text", "text": f"{query}"},]
            },
        ]
    
    messages.append(new_messages)
    inputs = tokenizer.apply_chat_template(
        messages,
        add_generation_prompt=True,
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
    ).to(device)


    with torch.inference_mode():
        outputs = model.generate(**inputs, max_new_tokens=64)

    outputs = tokenizer.batch_decode(outputs)
    print(outputs[0])

    # Extract only the model's response
    response = outputs[0]

    # Find the model's answer after <start_of_turn>model
    if "<start_of_turn>model" in response:
        model_answer = response.split("<start_of_turn>model")[1].split("<end_of_turn>")[0].strip()
    else:
        model_answer = -1

    print(f"Model answer: {model_answer}")

    return model_answer










# from transformers import AutoTokenizer, BitsAndBytesConfig, Gemma3ForCausalLM
# import torch
# import pandas as pd
# import json

# model_id = "google/gemma-3-1b-it"
# df = pd.read_csv("sample_transactions-2.csv")

# print(df.head())

# # Check if MPS performace shader for apple silicon available
# device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
# print(f"Using device: {device}")

# date = df['date'].tolist()
# amount =df['amount'].tolist()
# merchant =df['merchant'].tolist()
# description =df['description'].tolist()
# Answer = []
# Answer_reason = []
# # quantization_config = BitsAndBytesConfig(load_in_4bit=True)

# model = Gemma3ForCausalLM.from_pretrained(
#     model_id, 
#     # quantization_config=quantization_config
# ).to(device).eval()

# tokenizer = AutoTokenizer.from_pretrained(model_id)

# for i in range(20):
#     messages = [
#         [
#             {
#                 "role": "system",
#                 "content": [{"type": "text", "text": "You are a helpful assistant."},]
#             },
#             {
#                 "role": "user",
#                 "content": [{"type": "text", "text": f"Answer whether it's tax deductible or not as an employer. \n {date[i]} {amount[i]} {merchant[i]} {description[i]}\n Answer it Yes or No"},]
#             },
#         ],
#     ]

#     inputs = tokenizer.apply_chat_template(
#         messages,
#         add_generation_prompt=True,
#         tokenize=True,
#         return_dict=True,
#         return_tensors="pt",
#     ).to(device)


#     with torch.inference_mode():
#         outputs = model.generate(**inputs, max_new_tokens=64)

#     outputs = tokenizer.batch_decode(outputs)
#     print(outputs[0])
    
#     # Extract only the model's response
#     response = outputs[0]
    
#     # Find the model's answer after <start_of_turn>model
#     if "<start_of_turn>model" in response:
#         model_answer = response.split("<start_of_turn>model")[1].split("<end_of_turn>")[0].strip()
#     else:
#         model_answer = -1
    
#     print(f"Model answer: {model_answer}")
    
#     if model_answer.strip().split(".")[0] == "Yes":
#         Answer.append(1)
#     elif model_answer.strip().split(".")[0] == "No":
#         Answer.append(0)
#     else:
#         Answer.append(-1)
# for i in range(20):
#     messages = [
#         [
#             {
#                 "role": "system",
#                 "content": [{"type": "text", "text": "You are a helpful assistant."},]
#             },
#             {
#                 "role": "user",
#                 "content": [{"type": "text", "text": f"Answer why it's tax deductible or not as an employer in one sentence. \n {date[i]} {amount[i]} {merchant[i]} {description[i]}\n"},]
#             },
#         ],
#     ]

#     inputs = tokenizer.apply_chat_template(
#         messages,
#         add_generation_prompt=True,
#         tokenize=True,
#         return_dict=True,
#         return_tensors="pt",
#     ).to(device)


#     with torch.inference_mode():
#         outputs = model.generate(**inputs, max_new_tokens=64)

#     outputs = tokenizer.batch_decode(outputs)
#     print(outputs[0])
    
#     # Extract only the model's response
#     response = outputs[0]
    
#     # Find the model's answer after <start_of_turn>model
#     if "<start_of_turn>model" in response:
#         model_answer = response.split("<start_of_turn>model")[1].split("<end_of_turn>")[0].strip()
#     else:
#         model_answer = -1
    
#     print(f"Model answer: {model_answer}")
    
#     Answer_reason.append(f"{i}:{model_answer}\n\n")

# print(Answer)

# print(json.dumps(Answer_reason, indent=4))
