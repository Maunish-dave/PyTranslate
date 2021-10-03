# -*- coding: utf-8 -*-
"""Sequece to Sequence Model.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1-eCyZ10inQR5H7i-lhxe0HvU3rPRH6aW
"""

# !python3 -m spacy download en
# !python3 -m spacy download de
# !pip install colorama
# !pip install accelerate

import os
import time
import spacy
import random
import numpy as np
import pandas as pd

from tqdm.auto import tqdm 
from accelerate import Accelerator
from sklearn.model_selection import KFold

import torch
import torch.nn as nn
from torch.utils.data import DataLoader,Dataset

import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader

from colorama import Fore, Back, Style
r_ = Fore.RED
b_ = Fore.BLUE
c_ = Fore.CYAN
g_ = Fore.GREEN
y_ = Fore.YELLOW
m_ = Fore.MAGENTA
sr_ = Style.RESET_ALL

config = {'lr':0.001,
          'wd':0.0,
          'epochs':20,
          'nfolds':5,
          'max_len':15,
          'num_layers':2,
          'embed_size':200,
          'hidden_size':512,
          'dropout':0.3,
          'batch_size':64,
          'num_workers':2,
          'seed':1000}

for i in range(config['nfolds']):
    os.makedirs(f'model{i}',exist_ok=True)
    
def seed_everything(seed=42):
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = True

seed_everything(seed=config['seed'])

train_data = pd.read_csv("/content/drive/MyDrive/Colab Notebooks/Dataset/English to German/eng_to_ger_train.csv")
valid_data = pd.read_csv('/content/drive/MyDrive/Colab Notebooks/Dataset/English to German/eng_to_ger_valid.csv')

# train_data = train_data.sample(n=10000).reset_index(drop=True)

train_data['english'] = train_data['english'].apply(lambda x: x.replace('\n',' '))
train_data['german'] = train_data['german'].apply(lambda x: x.replace('\n',' '))

train_data['eng_len'] = train_data['english'].apply(lambda x: len(x.split()))
train_data['ger_len'] = train_data['german'].apply(lambda x: len(x.split()))

train_data['Fold'] = -1
kfold = KFold(n_splits=config['nfolds'],shuffle=True,random_state=config['seed'])
for k , (train_idx,valid_idx) in enumerate(kfold.split(X=train_data)):
    train_data.loc[valid_idx,'Fold'] = k

train_data.head()

train_data.eng_len.plot(kind='hist'),train_data.eng_len.mean()

class Tokenizer:
  def __init__(self,language = 'en',threshold=2):
    self.language = language
    self.threshold = threshold
    self.word_tokenizer = spacy.load(self.language)

    self.vocab = {"<PAD>":0,"<SOS>":1,"<EOS>":2,"<UNK>":3}
    self.idtostr = {0:"<PAD>",1:"<SOS>",2:"<EOS>",3:"<UNK>"}
    self.vocab_pad_idx = self.vocab['<PAD>']
    self.vocab_size = len(self.vocab)

  def train(self,sentences):
    frequency = {}
    idx = len(self.vocab.keys())

    for sentence in tqdm(sentences):
      words = [w.text.lower() for w in self.word_tokenizer.tokenizer(sentence)]
      for word in words:
        if word not in frequency:
          frequency[word] = 1
        else:
          frequency[word] += 1

        if frequency[word] == self.threshold:
          self.vocab[word] = idx
          idx += 1

    self.idtostr = { value:key for key,value in self.vocab.items() }
    self.vocab_size = len(self.vocab)

  def tokenize(self,text):
    words =  [word.text.lower() for word in self.word_tokenizer.tokenizer(text)]
    numerical_representation = [self.vocab["<SOS>"]]

    for word in words:
      if word in self.vocab:
        numerical_representation.append(self.vocab[word])
      else:
        numerical_representation.append(self.vocab['<UNK>'])
          
    return numerical_representation

class TranslatorDataset(Dataset):
  def __init__(self,df,tokenizer1,tokenizer2,max_len=100):
    self.english_sentences = df.english.tolist()
    self.german_sentences = df.german.tolist()

    self.tokenizer1 = tokenizer1
    self.tokenizer2 = tokenizer2

    self.max_len = max_len

  def __len__(self):
    return len(self.english_sentences)

  def padding(self,tokens,pad_idx,end_idx):
    pad_len = abs(self.max_len - len(tokens))
    tokens += [pad_idx] * pad_len
    tokens = tokens[:self.max_len-1]
    tokens += [end_idx]
    return tokens
  
  def __getitem__(self,index):
    eng_tokens = self.tokenizer1.tokenize(self.english_sentences[index])
    ger_tokens = self.tokenizer2.tokenize(self.german_sentences[index])

    eng_tokens = self.padding(eng_tokens,self.tokenizer1.vocab_pad_idx,self.tokenizer1.vocab['<EOS>'])
    ger_tokens = self.padding(ger_tokens,self.tokenizer2.vocab_pad_idx,self.tokenizer2.vocab['<EOS>'])

    eng_tokens = torch.tensor(eng_tokens)
    ger_tokens = torch.tensor(ger_tokens)

    return eng_tokens, ger_tokens

"""## Model"""

class Encoder(nn.Module):
  def __init__(self,vocab_size,embedding_size,hidden_size,num_layers,p=0.3):
    super(Encoder,self).__init__()
    self.embedding = nn.Embedding(vocab_size,embedding_size)
    self.lstm = nn.LSTM(embedding_size,hidden_size,num_layers,dropout=p)

  def forward(self,x):
    embedding = self.embedding(x)
    ouput,(hidden,cell) = self.lstm(embedding)
    return hidden ,cell

class Decoder(nn.Module):
  def __init__(self,vocab_size,embedding_size,hidden_size,num_layers,p=0.3):
    super(Decoder,self).__init__()
    self.vocab_size = vocab_size
    self.embedding = nn.Embedding(vocab_size,embedding_size)
    self.lstm = nn.LSTM(embedding_size,hidden_size,num_layers,dropout=p)
    self.fc = nn.Linear(hidden_size,vocab_size)

  def forward(self,x,hidden,cell):
    x = x.unsqueeze(0)

    embedding = self.embedding(x)
    output,(hidden,cell) = self.lstm(embedding,(hidden,cell))

    prediction = self.fc(output)
    
    prediction = prediction.squeeze(0)

    return prediction, hidden, cell

class SequencetoSequence(nn.Module):
  def __init__(self,encoder,decoder):
    super(SequencetoSequence,self).__init__()
    self.encoder = encoder
    self.decoder = decoder

  def translate(self,input,eos_idx):
    hidden,cell = self.encoder(input)

    batch_size = input.shape[1]
    target_len = input.shape[0]

    vocab_size = self.decoder.vocab_size
    outputs = torch.zeros(target_len,batch_size,vocab_size).to("cuda")
    x = input[0]

    for i in range(1,target_len):
      output,hidden,cell = self.decoder(x,hidden,cell)
      x = output.argmax(1)
      outputs[i] = output

    return outputs
  
  def forward(self,input,target):
    batch_size = input.shape[1]
    target_len = target.shape[0]

    vocab_size = self.decoder.vocab_size
    outputs = torch.zeros(target_len,batch_size,vocab_size).to("cuda")
    hidden,cell = self.encoder(input)

    x = target[0]
    for i in range(1,target_len):
      output,hidden,cell = self.decoder(x,hidden,cell)
      outputs[i] = output
      output_word = output.argmax(1)
      x = target[i] if random.random() < 0.5 else output_word
    
    return outputs

eng_tokenizer = Tokenizer(threshold=2)
eng_tokenizer.train(train_data.english.tolist())

ger_tokenizer = Tokenizer(language='de',threshold=2)
ger_tokenizer.train(train_data.german.tolist())

print(eng_tokenizer.vocab_size,ger_tokenizer.vocab_size)

eng_tokenizer.tokenize("hello how are you"), ger_tokenizer.tokenize("Eine Frau steht auf der Rotrunde ")

def run(fold):

    pad_idx = eng_tokenizer.vocab["<PAD>"]
    loss_fn = nn.CrossEntropyLoss(ignore_index=pad_idx)
    
    def evaluate(model,valid_loader):
        model.eval()
        valid_loss = 0
        with torch.no_grad():
            for i, (inputs,targets) in enumerate(tqdm(valid_loader)):
                inputs = inputs.permute((1,0))
                targets = targets.permute((1,0))
                outputs = model(inputs,targets)
                outputs = outputs[1:].reshape(-1,outputs.shape[2])
                targets = targets[1:].reshape(-1)
                loss = loss_fn(outputs,targets)
                valid_loss += loss.item()

        valid_loss /= len(valid_loader)
        return valid_loss
        
    def train_and_evaluate_loop(train_loader,valid_loader,model,optimizer,
                                epoch,fold,best_loss,lr_scheduler=None):
        train_loss = 0
        for i, (inputs,targets) in enumerate(tqdm(train_loader)):
            inputs = inputs.permute((1,0))
            targets = targets.permute((1,0))

            optimizer.zero_grad()
            model.train()

            outputs = model(inputs,targets)
            outputs = outputs[1:].reshape(-1,outputs.shape[2])
            targets = targets[1:].reshape(-1)
            loss = loss_fn(outputs,targets)
            loss.backward()
            
            optimizer.step()
            
            train_loss += loss.item()
            if lr_scheduler:
                lr_scheduler.step()
        
        train_loss /= len(train_loader)
        valid_loss = evaluate(model,valid_loader) 

        print(f"Epoch:{epoch} |Train Loss:{train_loss}|Valid Loss:{valid_loss}")
        if valid_loss <= best_loss:
            print(f"{g_}Loss Decreased from {best_loss} to {valid_loss}{sr_}")

            best_loss = valid_loss
            torch.save(model.state_dict(),f'./model{fold}/model{fold}.bin')
                    
        return best_loss
        
    accelerator = Accelerator()
    print(f"{accelerator.device} is used")
    
    x_train,x_valid = train_data.query(f"Fold != {fold}"),train_data.query(f"Fold == {fold}")
        
    eng_vocab_size=  eng_tokenizer.vocab_size
    ger_vocab_size= ger_tokenizer.vocab_size

    encoder = Encoder(eng_vocab_size,config['embed_size'],config['hidden_size'],config['num_layers'])
    decoder = Decoder(ger_vocab_size,config['embed_size'],config['hidden_size'],config['num_layers'])

    model = SequencetoSequence(encoder,decoder)

    train_ds = TranslatorDataset(x_train,eng_tokenizer,ger_tokenizer,max_len=config['max_len'])
    train_dl = DataLoader(train_ds,
                        batch_size = config["batch_size"],
                        num_workers = config['num_workers'],
                        shuffle=True,
                        pin_memory=True,
                        drop_last=False)
    
    valid_ds = TranslatorDataset(x_valid,eng_tokenizer,ger_tokenizer,max_len=config['max_len'])
    valid_dl = DataLoader(valid_ds,
                        batch_size = config["batch_size"],
                        num_workers = config['num_workers'],
                        shuffle=False,
                        pin_memory=True,
                        drop_last=False)

    optimizer = optim.Adam(model.parameters(),lr=config['lr'],weight_decay=config['wd'])    
    lr_scheduler = None

    model,train_dl,valid_dl,optimizer,lr_scheduler = accelerator.prepare(model,train_dl,valid_dl,optimizer,lr_scheduler)

    print(f"Fold: {fold}")
    best_loss = 9999
    start_time = time.time()
    for epoch in range(config["epochs"]):
        print(f"Epoch Started:{epoch}")
        best_loss = train_and_evaluate_loop(train_dl,valid_dl,model,optimizer,epoch,fold,best_loss,lr_scheduler)
        
        end_time = time.time()
        print(f"{m_}Time taken by epoch {epoch} is {end_time-start_time:.2f}s{sr_}")
        start_time = end_time
        
    return best_loss

# config['epochs'] = 5
run(0)

"""## Translate"""

def translate(test,model,eng_tokenizer,ger_tokenizer,eos_idx):
  model.eval()
  test_ds = TranslatorDataset(test,eng_tokenizer,ger_tokenizer,max_len=config['max_len'])
  test_dl = DataLoader(test_ds,
                      batch_size = 4,
                      num_workers = config['num_workers'],
                      shuffle=False,
                      pin_memory=True,
                      drop_last=False)
  
  outputs = []
  with torch.no_grad():
    for inputs,targets in test_dl:
      inputs = inputs.permute((1,0))
      output = model.translate(inputs,eos_idx)
      output = output.permute((1,0,2))
      output = output[:,1:].argmax(2).detach().cpu().numpy().tolist()
      outputs.extend(output)
  
  ger_vocab = ger_tokenizer.idtostr

  sentences = []
  for output in outputs:
    sentence = []
    for o in output:
      sentence.append(ger_vocab[o])
    sentences.append(' '.join(sentence[:-1]))

  return sentences

eng_vocab_size=  eng_tokenizer.vocab_size
ger_vocab_size= ger_tokenizer.vocab_size

encoder = Encoder(eng_vocab_size,config['embed_size'],config['hidden_size'],config['num_layers'])
decoder = Decoder(ger_vocab_size,config['embed_size'],config['hidden_size'],config['num_layers'])

model = SequencetoSequence(encoder,decoder)
model.load_state_dict(torch.load('/content/model0/model0.bin'))

eng_text = train_data.query(f"Fold != {0}")[:4]
ger_text = translate(eng_text,model,eng_tokenizer,ger_tokenizer,ger_tokenizer.vocab['<EOS>'])

eng_text = eng_text.english.tolist()

for eng,ger in zip(eng_text,ger_text):
  print(eng)
  print(ger)
  print()

