'''
Multitask BERT class, starter training code, evaluation, and test code.

Of note are:
* class MultitaskBERT: Your implementation of multitask BERT.
* function train_multitask: Training procedure for MultitaskBERT. Starter code
    copies training procedure from `classifier.py` (single-task SST).
* function test_multitask: Test procedure for MultitaskBERT. This function generates
    the required files for submission.

Running `python multitask_classifier.py` trains and tests your MultitaskBERT and
writes all required submission files.
'''

import random, numpy as np, argparse
from types import SimpleNamespace

import torch
from torch import nn
import torch.nn.functional as F
from torch.utils.data import DataLoader

from bert import BertModel
from optimizer import AdamW
from tqdm import tqdm
from tokenizer import BertTokenizer

from datasets import (
    SentenceClassificationDataset,
    SentenceClassificationTestDataset,
    SentencePairDataset,
    SentencePairTestDataset,
    load_multitask_data
)

from evaluation import model_eval_sst, model_eval_multitask, model_eval_test_multitask


TQDM_DISABLE=False


# Fix the random seed.
def seed_everything(seed=11711):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True


BERT_HIDDEN_SIZE = 768
N_SENTIMENT_CLASSES = 5


class MultitaskBERT(nn.Module):
    '''
    This module should use BERT for 3 tasks:

    - Sentiment classification (predict_sentiment)
    - Paraphrase detection (predict_paraphrase)
    - Semantic Textual Similarity (predict_similarity)
    '''
    def __init__(self, config):
        super(MultitaskBERT, self).__init__()
        self.bert = BertModel.from_pretrained('bert-base-uncased')
        self.tokenizer = BertTokenizer.from_pretrained('bert-large-uncased')
        # Pretrain mode does not require updating BERT paramters.
        for param in self.bert.parameters():
            if config.option == 'pretrain':
                param.requires_grad = False
            elif config.option == 'finetune':
                param.requires_grad = True
        # You will want to add layers here to perform the downstream tasks.
        self.dropout1 = nn.Dropout(config.hidden_dropout_prob)
        self.dropout2 = nn.Dropout(config.hidden_dropout_prob)
        self.dropout3 = nn.Dropout(config.hidden_dropout_prob)
        self.proj_sent = nn.Linear(config.hidden_size, config.num_labels)
        self.proj_para = nn.Linear(config.hidden_size, 1)
        self.proj_simi = nn.Linear(config.hidden_size, 1)
        self.ps_cosine = nn.CosineSimilarity(dim=1)
        self.ps_dropout_1 = nn.Dropout(config.hidden_dropout_prob)
        self.ps_dropout_2 = nn.Dropout(config.hidden_dropout_prob)
        self.ps_relu = nn.ReLU()


    def forward(self, input_ids, attention_mask):
        'Takes a batch of sentences and produces embeddings for them.'
        # The final BERT embedding is the hidden state of [CLS] token (the first token)
        # Here, you can start by just returning the embeddings straight from BERT.
        # When thinking of improvements, you can later try modifying this
        # (e.g., by adding other layers).
        pool_out = self.bert(input_ids, attention_mask)['pooler_output']
        return pool_out


    def predict_sentiment(self, input_ids, attention_mask):
        '''Given a batch of sentences, outputs logits for classifying sentiment.
        There are 5 sentiment classes:
        (0 - negative, 1- somewhat negative, 2- neutral, 3- somewhat positive, 4- positive)
        Thus, your output should contain 5 logits for each sentence.
        '''
        pool_out = self.forward(input_ids, attention_mask)
        out = self.dropout1(pool_out)
        logits = self.proj_sent(out)
        return logits

    def get_pair_embeddings(self, input_ids_1, attention_mask_1,
                           input_ids_2, attention_mask_2):
        '''Given a batch of pairs of sentences, get the embeddings.'''
        sep_token_id = torch.tensor([self.tokenizer.sep_token_id], dtype=torch.long, device=input_ids_1.device)
        batch_sep_token_id = sep_token_id.repeat(input_ids_1.shape[0], 1)
        input_id = torch.cat((input_ids_1, batch_sep_token_id, input_ids_2, batch_sep_token_id), dim=1)
        attention_mask = torch.cat((attention_mask_1, torch.ones_like(batch_sep_token_id), attention_mask_2, torch.ones_like(batch_sep_token_id)), dim=1)                  
        x = self.forward(input_id, attention_mask)

        return x
    
    def predict_paraphrase(self,
                           input_ids_1, attention_mask_1,
                           input_ids_2, attention_mask_2):
        '''Given a batch of pairs of sentences, outputs a single logit for predicting whether they are paraphrases.
        Note that your output should be unnormalized (a logit); it will be passed to the sigmoid function
        during evaluation.
        '''
        #pool_out_1 = self.forward(input_ids_1, attention_mask_1)
        #pool_out_2 = self.forward(input_ids_2, attention_mask_2)
        #diff = pool_out_1 - pool_out_2
        #out = self.dropout2(diff)
        #logit = self.proj_para(out)
        #return logit

        x = self.get_pair_embeddings(input_ids_1, attention_mask_1, input_ids_2, attention_mask_2)
        x = self.dropout2(x)
        x = self.proj_para(x)
        return x

    def predict_similarity(self,
                           input_ids_1, attention_mask_1,
                           input_ids_2, attention_mask_2):
        '''Given a batch of pairs of sentences, outputs a single logit corresponding to how similar they are.
        Note that your output should be unnormalized (a logit).
        '''
        # pool_out_1 = self.forward(input_ids_1, attention_mask_1)
        # outputs_1 = self.ps_dropout_1(pool_out_1)
        # pool_out_2 = self.forward(input_ids_2, attention_mask_2)
        # outputs_2 = self.ps_dropout_2(pool_out_2)
        # simscores = self.ps_cosine(outputs_1, outputs_2)
        # logit = self.ps_relu(simscores)*5
        # return logit
        #diff = pool_out_1 - pool_out_2
        #out = self.dropout3(diff)
        #logit = self.proj_simi(out)
        #return logit

        x = self.get_pair_embeddings(input_ids_1, attention_mask_1, input_ids_2, attention_mask_2)
        x = self.dropout3(x)
        x = self.proj_simi(x)
#        x = torch.sigmoid(x) * 6 - 0.5
        return x


def save_model(model, optimizer, args, config, filepath):
    save_info = {
        'model': model.state_dict(),
        'optim': optimizer.state_dict(),
        'args': args,
        'model_config': config,
        'system_rng': random.getstate(),
        'numpy_rng': np.random.get_state(),
        'torch_rng': torch.random.get_rng_state(),
    }

    torch.save(save_info, filepath)
    print(f"save the model to {filepath}")

def step_sst(batch,optimizer,model,device):
    b_ids, b_mask, b_labels = (batch['token_ids'],batch['attention_mask'], batch['labels'])

    b_ids = b_ids.to(device)
    b_mask = b_mask.to(device)
    b_labels = b_labels.to(device)

    optimizer.zero_grad()
    logits = model.predict_sentiment(b_ids, b_mask)
    loss = F.cross_entropy(logits, b_labels.view(-1), reduction='sum') / args.batch_size

    loss.backward()
    optimizer.step()
    return optimizer, loss

def step_para(batch,optimizer,model,device):
    b_ids_1, b_ids_2, b_mask_1, b_mask_2, b_labels = \
        batch['token_ids_1'], batch['token_ids_2'], batch['attention_mask_1'], batch['attention_mask_2'], batch['labels']
    b_ids_1 = b_ids_1.to(device)
    b_ids_2 = b_ids_2.to(device)
    b_mask_1 = b_mask_1.to(device)
    b_mask_2 = b_mask_2.to(device)
    b_labels = b_labels.to(device)

    optimizer.zero_grad()
    logits = model.predict_paraphrase(b_ids_1, b_mask_1,b_ids_2, b_mask_2)
    loss = F.binary_cross_entropy_with_logits(logits.view(-1), b_labels.float(), reduction='sum') / args.batch_size
    loss.backward()
    optimizer.step()
    return optimizer, loss

def step_sts(batch,optimizer,model,device):
    b_ids_1, b_ids_2, b_mask_1, b_mask_2, b_labels = \
        batch['token_ids_1'], batch['token_ids_2'], batch['attention_mask_1'], batch['attention_mask_2'], batch[
                'labels']
    b_ids_1 = b_ids_1.to(device)
    b_ids_2 = b_ids_2.to(device)
    b_mask_1 = b_mask_1.to(device)
    b_mask_2 = b_mask_2.to(device)
    b_labels = b_labels.to(device)

    optimizer.zero_grad()
    logits = model.predict_similarity(b_ids_1, b_mask_1, b_ids_2, b_mask_2)
    #b_labels = (b_labels-2.5)/5
    #print(logits)
    #print(b_labels)
    loss = F.mse_loss(logits.view(-1), b_labels.float(), reduction='sum') / args.batch_size
    loss.backward()
    optimizer.step()
    return optimizer, loss

def train_multitask(args):
    '''Train MultitaskBERT.

    Currently only trains on SST dataset. The way you incorporate training examples
    from other datasets into the training procedure is up to you. To begin, take a
    look at test_multitask below to see how you can use the custom torch `Dataset`s
    in datasets.py to load in examples from the Quora and SemEval datasets.
    '''
    device = torch.device('cuda') if args.use_gpu else torch.device('cpu')
    # Create the data and its corresponding datasets and dataloader.
    sst_train_data, num_labels,para_train_data, sts_train_data = load_multitask_data(args.sst_train,args.para_train,args.sts_train, split ='train')
    sst_dev_data, num_labels,para_dev_data, sts_dev_data = load_multitask_data(args.sst_dev,args.para_dev,args.sts_dev, split ='train')
    num_labels = len(num_labels)
    sst_train_data = SentenceClassificationDataset(sst_train_data, args)
    sst_dev_data = SentenceClassificationDataset(sst_dev_data, args)

    sst_train_dataloader = DataLoader(sst_train_data, shuffle=True, batch_size=args.batch_size,
                                      collate_fn=sst_train_data.collate_fn)
    sst_dev_dataloader = DataLoader(sst_dev_data, shuffle=False, batch_size=args.batch_size,
                                    collate_fn=sst_dev_data.collate_fn)

    para_train_data = SentencePairDataset(para_train_data, args)
    para_dev_data = SentencePairDataset(para_dev_data, args)

    para_train_dataloader = DataLoader(para_train_data, shuffle=True, batch_size=args.batch_size,
                                      collate_fn=para_train_data.collate_fn)
    para_dev_dataloader = DataLoader(para_dev_data, shuffle=False, batch_size=args.batch_size,
                                     collate_fn=para_dev_data.collate_fn)

    sts_train_data = SentencePairDataset(sts_train_data, args, isRegression=True)
    sts_dev_data = SentencePairDataset(sts_dev_data, args, isRegression=True)

    sts_train_dataloader = DataLoader(sts_train_data, shuffle=True, batch_size=args.batch_size,
                                     collate_fn=sts_train_data.collate_fn)
    sts_dev_dataloader = DataLoader(sts_dev_data, shuffle=False, batch_size=args.batch_size,
                                    collate_fn=sts_dev_data.collate_fn)


    # Init model.
    config = {'hidden_dropout_prob': args.hidden_dropout_prob,
              'num_labels': num_labels,
              'hidden_size': 768,
              'data_dir': '.',
              'option': args.option}

    config = SimpleNamespace(**config)

    model = MultitaskBERT(config)
    model = model.to(device)

    lr = args.lr
    optimizer = AdamW(model.parameters(), lr=lr)
    best_dev_acc = 0

    # Run for the specified number of epochs.
    for epoch in range(args.epochs):
        model.train()
        train_loss = 0
        num_batches = 0
        num_batches_sst, num_batches_para, num_batches_sts = len(sst_train_dataloader),len(para_train_dataloader),len(sts_train_dataloader)
        num_batches_total = num_batches_sst + num_batches_para + num_batches_sts
        #positions = [0,0,0]
        dataloaders = {'sst':sst_train_dataloader, 'para':para_train_dataloader, 'sts':sts_train_dataloader}
        step_funcs = {'sst':step_sst, 'para':step_para, 'sts':step_sts}
        keys_loaders = ('sst','para','sts')\
        # shuffle the batches
        sst_indicators = torch.zeros(num_batches_sst,dtype=int,device=device)
        para_indicators = torch.zeros(num_batches_para,dtype=int,device=device) + 1
        sts_indicators = torch.zeros(num_batches_sts,dtype=int,device=device) + 2
        task_indicators = torch.concat((sst_indicators,para_indicators,sts_indicators),dim=0)
        task_indicators.to(device)
        task_indexes = torch.randperm(task_indicators.shape[0])
        task_indexes.to(device)
        task_indicators = task_indicators[task_indexes]

        for i in tqdm(range(num_batches_total)):
            task_type = task_indicators[i] # int
            task_key = keys_loaders[task_type] #str
            #position = positions[task_type]
            batch = next(iter(dataloaders[task_key]))
            #positions[task_type] = positions[task_type] + 1
            optimizer,loss = step_funcs[task_key](batch,optimizer,model,device)
            train_loss += loss.item()
            num_batches += 1

        train_loss = train_loss / (num_batches)

        #train_acc, train_f1, *_ = model_eval_sst(sst_train_dataloader, model, device)
        # sst_train_acc, sst_y_pred, sst_sent_ids,\
        # para_train_acc, para_y_pred, para_sent_ids,\
        # sts_train_corr, sts_y_pred, sts_sent_ids = model_eval_multitask(sst_train_dataloader,\
        #                                                                 para_train_dataloader,\
        #                                                                 sts_train_dataloader,model, device)
        #train_acc = (sst_train_acc + para_train_acc + sts_train_corr) / 3
        sst_dev_acc, sst_y_pred, sst_sent_ids, \
        para_dev_acc, para_y_pred, para_sent_ids, \
        sts_dev_corr, sts_y_pred, sts_sent_ids = model_eval_multitask(sst_dev_dataloader, \
                                                                        para_dev_dataloader, \
                                                                        sts_dev_dataloader, model, device)
        dev_acc = (sst_dev_acc+para_dev_acc+sts_dev_corr)/3
        if dev_acc > best_dev_acc:
            best_dev_acc = dev_acc
            save_model(model, optimizer, args, config, args.filepath)

#        print(f"Epoch {epoch}: train loss :: {train_loss :.3f}, train acc :: {train_acc :.3f}, dev acc :: {dev_acc :.3f}")


def test_multitask(args):
    '''Test and save predictions on the dev and test sets of all three tasks.'''
    with torch.no_grad():
        device = torch.device('cuda') if args.use_gpu else torch.device('cpu')
        saved = torch.load(args.filepath)
        config = saved['model_config']

        model = MultitaskBERT(config)
        model.load_state_dict(saved['model'])
        model = model.to(device)
        print(f"Loaded model to test from {args.filepath}")

        sst_test_data, num_labels,para_test_data, sts_test_data = \
            load_multitask_data(args.sst_test,args.para_test, args.sts_test, split='test')

        sst_dev_data, num_labels,para_dev_data, sts_dev_data = \
            load_multitask_data(args.sst_dev,args.para_dev,args.sts_dev,split='dev')

        sst_test_data = SentenceClassificationTestDataset(sst_test_data, args)
        sst_dev_data = SentenceClassificationDataset(sst_dev_data, args)

        sst_test_dataloader = DataLoader(sst_test_data, shuffle=True, batch_size=args.batch_size,
                                         collate_fn=sst_test_data.collate_fn)
        sst_dev_dataloader = DataLoader(sst_dev_data, shuffle=False, batch_size=args.batch_size,
                                        collate_fn=sst_dev_data.collate_fn)

        para_test_data = SentencePairTestDataset(para_test_data, args)
        para_dev_data = SentencePairDataset(para_dev_data, args)

        para_test_dataloader = DataLoader(para_test_data, shuffle=True, batch_size=args.batch_size,
                                          collate_fn=para_test_data.collate_fn)
        para_dev_dataloader = DataLoader(para_dev_data, shuffle=False, batch_size=args.batch_size,
                                         collate_fn=para_dev_data.collate_fn)

        sts_test_data = SentencePairTestDataset(sts_test_data, args)
        sts_dev_data = SentencePairDataset(sts_dev_data, args, isRegression=True)

        sts_test_dataloader = DataLoader(sts_test_data, shuffle=True, batch_size=args.batch_size,
                                         collate_fn=sts_test_data.collate_fn)
        sts_dev_dataloader = DataLoader(sts_dev_data, shuffle=False, batch_size=args.batch_size,
                                        collate_fn=sts_dev_data.collate_fn)

        dev_sentiment_accuracy,dev_sst_y_pred, dev_sst_sent_ids, \
            dev_paraphrase_accuracy, dev_para_y_pred, dev_para_sent_ids, \
            dev_sts_corr, dev_sts_y_pred, dev_sts_sent_ids = model_eval_multitask(sst_dev_dataloader,
                                                                    para_dev_dataloader,
                                                                    sts_dev_dataloader, model, device)

        test_sst_y_pred, \
            test_sst_sent_ids, test_para_y_pred, test_para_sent_ids, test_sts_y_pred, test_sts_sent_ids = \
                model_eval_test_multitask(sst_test_dataloader,
                                          para_test_dataloader,
                                          sts_test_dataloader, model, device)

        with open(args.sst_dev_out, "w+") as f:
            print(f"dev sentiment acc :: {dev_sentiment_accuracy :.3f}")
            f.write(f"id \t Predicted_Sentiment \n")
            for p, s in zip(dev_sst_sent_ids, dev_sst_y_pred):
                f.write(f"{p} , {s} \n")

        with open(args.sst_test_out, "w+") as f:
            f.write(f"id \t Predicted_Sentiment \n")
            for p, s in zip(test_sst_sent_ids, test_sst_y_pred):
                f.write(f"{p} , {s} \n")

        with open(args.para_dev_out, "w+") as f:
            print(f"dev paraphrase acc :: {dev_paraphrase_accuracy :.3f}")
            f.write(f"id \t Predicted_Is_Paraphrase \n")
            for p, s in zip(dev_para_sent_ids, dev_para_y_pred):
                f.write(f"{p} , {s} \n")

        with open(args.para_test_out, "w+") as f:
            f.write(f"id \t Predicted_Is_Paraphrase \n")
            for p, s in zip(test_para_sent_ids, test_para_y_pred):
                f.write(f"{p} , {s} \n")

        with open(args.sts_dev_out, "w+") as f:
            print(f"dev sts corr :: {dev_sts_corr :.3f}")
            f.write(f"id \t Predicted_Similiary \n")
            for p, s in zip(dev_sts_sent_ids, dev_sts_y_pred):
                f.write(f"{p} , {s} \n")

        with open(args.sts_test_out, "w+") as f:
            f.write(f"id \t Predicted_Similiary \n")
            for p, s in zip(test_sts_sent_ids, test_sts_y_pred):
                f.write(f"{p} , {s} \n")


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sst_train", type=str, default="data/ids-sst-train.csv")
    parser.add_argument("--sst_dev", type=str, default="data/ids-sst-dev.csv")
    parser.add_argument("--sst_test", type=str, default="data/ids-sst-test-student.csv")

    parser.add_argument("--para_train", type=str, default="data/quora-train.csv")
    parser.add_argument("--para_dev", type=str, default="data/quora-dev.csv")
    parser.add_argument("--para_test", type=str, default="data/quora-test-student.csv")

    parser.add_argument("--sts_train", type=str, default="data/sts-train.csv")
    parser.add_argument("--sts_dev", type=str, default="data/sts-dev.csv")
    parser.add_argument("--sts_test", type=str, default="data/sts-test-student.csv")

    parser.add_argument("--seed", type=int, default=11711)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--option", type=str,
                        help='pretrain: the BERT parameters are frozen; finetune: BERT parameters are updated',
                        choices=('pretrain', 'finetune'), default="pretrain")
    parser.add_argument("--use_gpu", action='store_true')

    parser.add_argument("--sst_dev_out", type=str, default="predictions/sst-dev-output.csv")
    parser.add_argument("--sst_test_out", type=str, default="predictions/sst-test-output.csv")

    parser.add_argument("--para_dev_out", type=str, default="predictions/para-dev-output.csv")
    parser.add_argument("--para_test_out", type=str, default="predictions/para-test-output.csv")

    parser.add_argument("--sts_dev_out", type=str, default="predictions/sts-dev-output.csv")
    parser.add_argument("--sts_test_out", type=str, default="predictions/sts-test-output.csv")

    parser.add_argument("--batch_size", help='sst: 64, cfimdb: 8 can fit a 12GB GPU', type=int, default=8)
    parser.add_argument("--hidden_dropout_prob", type=float, default=0.3)
    parser.add_argument("--lr", type=float, help="learning rate", default=1e-5)

    args = parser.parse_args()
    return args


if __name__ == "__main__":
    args = get_args()
    args.filepath = f'{args.option}-{args.epochs}-{args.lr}-multitask.pt' # Save path.
    seed_everything(args.seed)  # Fix the seed for reproducibility.
    train_multitask(args)
    test_multitask(args)
