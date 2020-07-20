#!/usr/bin/env bash
export PYTHONPATH="../":"${PYTHONPATH}"

python finetune.py \
    --learning_rate=3e-5 \
    --fp16 \
    --gpus 1 \
    --do_train \
    --do_predict \
    --val_check_interval 0.25 \
    --adam_eps 1e-06 \
    --num_train_epochs 3 --src_lang en_XX --tgt_lang ro_RO \
    --freeze_embeds --data_dir $ENRO_DIR \
    --max_source_length $MAX_LEN --max_target_length $MAX_LEN --val_max_target_length $MAX_LEN --test_max_target_length $MAX_LEN \
    --train_batch_size=$BS --eval_batch_size=$BS --gradient_accumulation_steps=$GAS \
    --model_name_or_path facebook/mbart-large-cc25 \
    --task translation \
    --warmup_steps 500 \
    --sortish_sampler \
    $@
