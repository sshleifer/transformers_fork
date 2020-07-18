import itertools
import json
import linecache
import os
import pickle
from logging import getLogger
from pathlib import Path
from typing import Callable, Dict, Iterable, List

import git
import numpy as np
import torch
import torch.nn.functional as F
from rouge_score import rouge_scorer, scoring
from sacrebleu import corpus_bleu
from torch import nn
from torch.utils.data import Dataset, Sampler
from tqdm import tqdm

from transformers import BartTokenizer


def label_smoothed_nll_loss(lprobs, target, epsilon, ignore_index=-100, reduce=True):
    if target.dim() == lprobs.dim() - 1:
        target = target.unsqueeze(-1)
    nll_loss = -lprobs.gather(dim=-1, index=target)
    smooth_loss = -lprobs.sum(dim=-1, keepdim=True)
    if ignore_index is not None:
        pad_mask = target.eq(ignore_index)
        nll_loss.masked_fill_(pad_mask, 0.0)
        smooth_loss.masked_fill_(pad_mask, 0.0)
    else:
        nll_loss = nll_loss.squeeze(-1)
        smooth_loss = smooth_loss.squeeze(-1)
    if reduce:
        nll_loss = nll_loss.sum()  #mean()? Scared to break other math.
        smooth_loss = smooth_loss.sum()
    eps_i = epsilon / lprobs.size(-1)
    loss = (1.0 - epsilon) * nll_loss + eps_i * smooth_loss
    return loss/lprobs.shape[0], nll_loss/lprobs.shape[0]


def ce_loss(lm_logits, labels, **kwargs):
    masked_lm_loss = F.cross_entropy(lm_logits.view(-1, lm_logits.shape[-1]), labels.view(-1), **kwargs)
    return masked_lm_loss


def encode_line(tokenizer, line, max_length, pad_to_max_length=True, return_tensors="pt"):
    #TODO(SS): test that this returns max_length
    extra_kw = {"add_prefix_space": True} if isinstance(tokenizer, BartTokenizer) else {}
    return tokenizer(
        [line],
        max_length=max_length,
        padding="max_length" if pad_to_max_length else None,
        truncation=True,
        return_tensors=return_tensors,
        **extra_kw,
    )

def encode_file(
    tokenizer,
    data_path,
    max_length,
    pad_to_max_length=True,
    return_tensors="pt",
    overwrite_cache=False,
    prefix="",
    tok_name="",
):
    extra_kw = {"add_prefix_space": True} if isinstance(tokenizer, BartTokenizer) else {}
    cache_path = Path(f"{data_path}_{tok_name}{max_length}.pt")
    if not overwrite_cache and cache_path.exists():
        try:
            examples = torch.load(cache_path)
            assert isinstance(examples, list)
            return examples

        except Exception:
            print(f"failed to load from {cache_path}, retokenizing {data_path}")
    data_path = Path(data_path)

    lns = lmap(str.strip, data_path.open().readlines())
    lns = [prefix + text for text in lns]
    assert lns, f"found empty file at {data_path}"
    examples = []
    for text in tqdm(lns, desc=f"Tokenizing {data_path.name}"):
        tokenized = tokenizer(
            [text],
            max_length=max_length,
            padding="max_length" if pad_to_max_length else None,
            truncation=True,
            return_tensors=return_tensors,
            **extra_kw,
        )
        assert tokenized.input_ids.shape[1] == max_length
        examples.append(tokenized)
    torch.save(lmap(dict, examples), cache_path.open("wb"))
    return examples


def lmap(f: Callable, x: Iterable) -> List:
    """list(map(f, x))"""
    return list(map(f, x))


def calculate_bleu_score(output_lns, refs_lns, **kwargs) -> dict:
    """Uses sacrebleu's corpus_bleu implementation."""
    return {"bleu": corpus_bleu(output_lns, [refs_lns], **kwargs).score}


def trim_batch(
    input_ids, pad_token_id, attention_mask=None,
):
    """Remove columns that are populated exclusively by pad_token_id"""
    keep_column_mask = input_ids.ne(pad_token_id).any(dim=0)
    if attention_mask is None:
        return input_ids[:, keep_column_mask]
    else:
        return (input_ids[:, keep_column_mask], attention_mask[:, keep_column_mask])

from pathlib import Path
from transformers import MBartTokenizer
class MTDataset(Dataset):
    def __init__(
        self,
        tokenizer,
        data_dir,
        type_path="train",
        max_source_length=1024,
        max_target_length=56,
        n_obs=None,
        overwrite_cache=False,
        prefix="",
        src_lang=None,
        tgt_lang=None,
    ):
        super().__init__()
        self.max_source_length = max_source_length
        self.max_target_length = max_target_length
        self.src_file = Path(data_dir).joinpath(type_path + ".source")
        self.tgt_file = Path(data_dir).joinpath(type_path + ".target")
        self.seq_lens = self.get_char_lens(self.src_file)
        assert min(self.seq_lens) > 0
        if isinstance(tokenizer, MBartTokenizer):
            assert src_lang is not None
            assert tgt_lang is not None

        self.tokenizer = tokenizer
        self.tok_kw = {'src_lang': src_lang, 'tgt_lang': tgt_lang}
        if n_obs is not None:
            self.len = n_obs
        self.pad_token_id = self.tokenizer.pad_token_id

    def __len__(self):
        return len(self.seq_lens)

    def __getitem__(self, index):
        index = index + 1  # linecache starts at 1
        if index > len(self):
            raise ValueError(f'got index: {index}, length only {len(self)}')
        source_line = linecache.getline(str(self.src_file), index).rstrip("\n")
        tgt_line = linecache.getline(str(self.tgt_file), index).rstrip("\n")
        assert source_line, f'empty source line for index {index}'
        assert tgt_line, f'empty tgt line for index {index}'
        return {
            "src_texts": source_line,
            "tgt_texts": tgt_line,
            #"decoder_input_ids": target_ids,
        }

    @staticmethod
    def get_char_lens(data_file):
        return [len(x) for x in Path(data_file).open().readlines()]

    @staticmethod
    def trim_seq2seq_batch(batch, pad_token_id):
        y = trim_batch(batch["decoder_input_ids"], pad_token_id)
        source_ids, source_mask = trim_batch(batch["input_ids"], pad_token_id, attention_mask=batch["attention_mask"])
        return source_ids, source_mask, y

    def collate_fn(self, batch) -> dict:
        batch = self.tokenizer.prepare_translation_batch(
            src_texts=[x['src_texts'] for x in batch], tgt_texts=[x['tgt_texts'] for x in batch],
            max_length=self.max_source_length,
            **self.tok_kw)
        return batch

    def make_sortish_sampler(self, batch_size):
        return SortishSampler(self.seq_lens, batch_size)


class SortishSampler(Sampler):
    "Go through the text data by order of src length with a bit of randomness. From fastai repo."

    def __init__(self, data, batch_size):
        self.data, self.bs = data, batch_size

    def key(self, i):
        return self.data[i]

    def __len__(self) -> int:
        return len(self.data)

    def __iter__(self):
        idxs = np.random.permutation(len(self.data))
        sz = self.bs * 50
        ck_idx = [idxs[i : i + sz] for i in range(0, len(idxs), sz)]
        sort_idx = np.concatenate([sorted(s, key=self.key, reverse=True) for s in ck_idx])
        sz = self.bs
        ck_idx = [sort_idx[i : i + sz] for i in range(0, len(sort_idx), sz)]
        max_ck = np.argmax([self.key(ck[0]) for ck in ck_idx])  # find the chunk with the largest key,
        ck_idx[0], ck_idx[max_ck] = ck_idx[max_ck], ck_idx[0]  # then make sure it goes first.
        sort_idx = np.concatenate(np.random.permutation(ck_idx[1:])) if len(ck_idx) > 1 else np.array([], dtype=np.int)
        sort_idx = np.concatenate((ck_idx[0], sort_idx))
        return iter(sort_idx)


logger = getLogger(__name__)


def use_task_specific_params(model, task):
    """Update config with summarization specific params."""
    task_specific_params = model.config.task_specific_params

    if task_specific_params is not None:
        pars = task_specific_params.get(task, {})
        logger.info(f"using task specific params for {task}: {pars}")
        model.config.update(pars)


def pickle_load(path):
    """pickle.load(path)"""
    with open(path, "rb") as f:
        return pickle.load(f)


def pickle_save(obj, path):
    """pickle.dump(obj, path)"""
    with open(path, "wb") as f:
        return pickle.dump(obj, f)


def flatten_list(summary_ids: List[List]):
    return [x for x in itertools.chain.from_iterable(summary_ids)]


def save_git_info(folder_path: str) -> None:
    """Save git information to output_dir/git_log.json"""
    repo_infos = get_git_info()
    save_json(repo_infos, os.path.join(folder_path, "git_log.json"))


def save_json(content, path):
    with open(path, "w") as f:
        json.dump(content, f, indent=4)


def load_json(path):
    with open(path) as f:
        return json.load(f)


def get_git_info():
    repo = git.Repo(search_parent_directories=True)
    repo_infos = {
        "repo_id": str(repo),
        "repo_sha": str(repo.head.object.hexsha),
        "repo_branch": str(repo.active_branch),
    }
    return repo_infos


ROUGE_KEYS = ["rouge1", "rouge2", "rougeL"]


def calculate_rouge(output_lns: List[str], reference_lns: List[str], use_stemmer=True) -> Dict:
    scorer = rouge_scorer.RougeScorer(ROUGE_KEYS, use_stemmer=use_stemmer)
    aggregator = scoring.BootstrapAggregator()

    for reference_ln, output_ln in zip(reference_lns, output_lns):
        scores = scorer.score(reference_ln, output_ln)
        aggregator.add_scores(scores)

    result = aggregator.aggregate()
    return {k: v.mid.fmeasure for k, v in result.items()}


def freeze_params(model: nn.Module):
    for par in model.parameters():
        par.requires_grad = False


def grad_status(model: nn.Module) -> Iterable:
    return (par.requires_grad for par in model.parameters())


def any_requires_grad(model: nn.Module) -> bool:
    return any(grad_status(model))


def assert_all_frozen(model):
    model_grads: List[bool] = list(grad_status(model))
    n_require_grad = sum(lmap(int, model_grads))
    npars = len(model_grads)
    assert not any(model_grads), f"{n_require_grad/npars:.1%} of {npars} weights require grad"


def assert_not_all_frozen(model):
    model_grads: List[bool] = list(grad_status(model))
    npars = len(model_grads)
    assert any(model_grads), f"none of {npars} weights require grad"
