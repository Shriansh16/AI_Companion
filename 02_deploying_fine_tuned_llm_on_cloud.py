import modal
from modal import App, Volume, Image

# Setup - define our infrastructure with code!
app = modal.App("new-ai-companion_llama_3_1")
volume = Volume.from_name("model-cache", create_if_missing=True)  # Correctly define volume
image = Image.debian_slim().pip_install("huggingface", "torch", "transformers", "bitsandbytes", "accelerate", "peft", "langchain", "langchain_core","langchain_community")
secrets = [modal.Secret.from_name("huggingface-secret")]

# Constants
GPU = "A100"
BASE_MODEL = "meta-llama/Llama-3.1-8B-Instruct"
PROJECT_NAME = "AI COMPANION"
FINETUNED_MODEL = "MLsheenu/AI_COMPANION_finetuned_llama_3_1"

@app.cls(image=image, secrets=secrets, gpu=GPU, timeout=1800, volumes={"/cache": volume})  # Attach volume here
class Companion:
    @modal.build()
    def download_model_and_tokenizer(self):
        from huggingface_hub import snapshot_download
        import os

        MODEL_DIR = "/cache/models"  # Use the shared volume path
        os.makedirs(MODEL_DIR, exist_ok=True)
        
        # Download base and fine-tuned models and tokenizer
        snapshot_download(BASE_MODEL, local_dir=f"{MODEL_DIR}/{BASE_MODEL.split('/')[-1]}")
        snapshot_download(FINETUNED_MODEL, local_dir=f"{MODEL_DIR}/{FINETUNED_MODEL.split('/')[-1]}")
        
        # Download and store the tokenizer
        snapshot_download(BASE_MODEL, local_dir=f"{MODEL_DIR}/{BASE_MODEL.split('/')[-1]}/tokenizer")

    @modal.enter()
    def setup(self):
        import os
        import torch
        from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
        from peft import PeftModel

        # Set up paths
        MODEL_DIR = "/cache/models"
        base_model_path = f"{MODEL_DIR}/{BASE_MODEL.split('/')[-1]}"
        fine_tuned_model_path = f"{MODEL_DIR}/{FINETUNED_MODEL.split('/')[-1]}"
        tokenizer_path = f"{base_model_path}/tokenizer"  # Tokenizer directory path
        
        # Quant Config
        quant_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_quant_type="nf4"
        )
    
        # Load model and tokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(tokenizer_path)  # Load tokenizer from shared volume
        self.tokenizer.pad_token = self.tokenizer.eos_token
        self.tokenizer.padding_side = "right"
        
        self.base_model = AutoModelForCausalLM.from_pretrained(
            base_model_path, 
            quantization_config=quant_config,
            device_map="auto"
        )
        self.base_model.generation_config.pad_token_id = self.tokenizer.pad_token_id
    
        self.fine_tuned_model = PeftModel.from_pretrained(self.base_model, fine_tuned_model_path)

    @modal.method()  # No volumes needed here as it uses preloaded models
    def query(self, text: str, session_id: str) -> str:
      from langchain.memory import ChatMessageHistory
      from langchain.schema import SystemMessage, HumanMessage
      import re
      import torch

      # Store for session-based history
      if not hasattr(self, "history_store"):
        self.history_store = {}

      # Fetch or create session-specific history
      if session_id not in self.history_store:
          self.history_store[session_id] = ChatMessageHistory()

      # Add the new user query to history
      self.history_store[session_id].add_message(HumanMessage(content=text))

      # Construct the conversation history for the prompt
      conversation_history = "\n".join(
          f" {message.type.capitalize()}: {message.content}"
          for message in self.history_store[session_id].messages[-3:]
        )

      # Construct the full prompt
      prompt = f"""
        Conversation History:
        {conversation_history}
        please continue this conversation and give only one response
        """

      # Tokenize the input prompt
      input_ids = self.tokenizer(prompt, return_tensors="pt", truncation=True).input_ids.cuda()

      # Generate the response
      outputs = self.fine_tuned_model.generate(
        input_ids=input_ids,
        max_new_tokens=200,
        do_sample=True,
        repetition_penalty=1.2,
        top_p=0.7,
        temperature=0.3,
        eos_token_id=self.tokenizer.eos_token_id,
        pad_token_id=self.tokenizer.pad_token_id
      )

      # Decode the generated output
      response = self.tokenizer.batch_decode(outputs.detach().cpu().numpy(), skip_special_tokens=True)[0][len(prompt):]
      cleaned_response = response.split("### Response:")[0].strip()
      stop_patterns = ["```", "###", "How can I", "def ","Please respond accordingly","Note :","I hope this response","))","Please provide me with","Example response:","Please respond","Answer:","Please provide"]
      for pattern in stop_patterns:
       if pattern in cleaned_response:
        cleaned_response = cleaned_response.split(pattern)[0].strip()

      # Add AI response to history
      self.history_store[session_id].add_message(SystemMessage(content=cleaned_response))
      cleaned_response = re.sub(r'(?i)response:', '', cleaned_response)

      # Return the cleaned-up AI response
      return cleaned_response










    @modal.method()
    def wake_up(self) -> str:
        return "ok"

