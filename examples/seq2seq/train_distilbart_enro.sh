#!/usr/bin/env bash
export PYTHONPATH="../":"${PYTHONPATH}"
export BS=16
export GAS=2
python distillation.py \
  --learning_rate=3e-4 \
  --do_train \
  --do_predict \
  --fp16 \
  --val_check_interval 0.1 --n_val 1000 \
  --teacher facebook/mbart-large-en-ro --data_dir $ENRO_DIR \
  --freeze_encoder --freeze_embeds --data_dir $ENRO_DIR \
  --max_source_length $MAX_LEN --max_target_length $MAX_LEN --val_max_target_length $MAX_LEN --test_max_target_length $MAX_LEN \
  --student_decoder_layers 6 --student_encoder_layers 12 \
  --freeze_encoder --freeze_embeds \
  --model_name_or_path IGNORED \
  --alpha_hid=3. \
  --train_batch_size=$BS --eval_batch_size=$BS --gradient_accumulation_steps=$GAS --num_train_epochs=6 \
  --tokenizer_name facebook/mbart-large--cc25 \
  --warmup_steps 500 \
  --output_dir distilbart_enro_12_6 \
  $@
