#!/bin/bash
# API 响应速度对比测试
# 用法: ./benchmark_api_speed.sh

API_ENDPOINT="http://127.0.0.1:4100/v1/chat/completions"

test_model() {
    local model=$1
    local max_tokens=$2
    local test_name=$3

    echo "========================================" >&2
    echo "测试: $test_name" >&2
    echo "模型: $model" >&2
    echo "max_tokens: $max_tokens" >&2
    echo "========================================" >&2

    local start=$(date +%s.%N)

    response=$(curl -s -X POST "$API_ENDPOINT" \
        -H "Content-Type: application/json" \
        -d "{
            \"model\": \"$model\",
            \"messages\": [{\"role\": \"user\", \"content\": \"Write a complete Ascend C kernel for matrix multiplication. Include all necessary headers, queue management, tiling strategy, and error handling.\"}],
            \"max_tokens\": $max_tokens,
            \"stream\": false
        }" 2>&1)

    local end=$(date +%s.%N)
    local elapsed=$(echo "$end - $start" | bc)

    # 检查响应是否包含错误
    if echo "$response" | grep -q '"error"'; then
        echo "❌ 错误响应: $model (${elapsed}s)" >&2
        echo "$response" | jq -r '.error.message // .error' >&2
        echo "$model,error,$elapsed" >> api_benchmark_results.csv
    else
        local tokens=$(echo "$response" | jq -r '.usage.completion_tokens // 0')
        local tokens_per_sec=$(echo "scale=2; $tokens / $elapsed" | bc)
        echo "✅ 成功: $model" >&2
        echo "   耗时: ${elapsed}s" >&2
        echo "   生成: $tokens tokens" >&2
        echo "   速度: $tokens_per_sec tokens/s" >&2
        echo "$model,success,$elapsed,$tokens,$tokens_per_sec" >> api_benchmark_results.csv
    fi

    echo "" >&2
}

# 初始化结果文件
echo "model,status,elapsed_time,tokens,tokens_per_sec" > api_benchmark_results.csv

# 测试 1: 简单请求（10 tokens）
echo "==============================================" >&2
echo "测试 1: 简单请求（Hello）" >&2
echo "==============================================" >&2
test_model "deepseek/deepseek-flash" 10 "简单请求-deepseek-flash"
test_model "qwen3.8-27b" 10 "简单请求-qwen3.8-27b"

# 测试 2: 中等复杂度（512 tokens）
echo "==============================================" >&2
echo "测试 2: 中等复杂度（512 tokens）" >&2
echo "==============================================" >&2
test_model "deepseek/deepseek-flash" 512 "中等复杂-deepseek-flash"
test_model "qwen3.8-27b" 512 "中等复杂-qwen3.8-27b"

# 测试 3: 复杂请求（2048 tokens，模拟 prepare_operator）
echo "==============================================" >&2
echo "测试 3: 复杂请求（2048 tokens）" >&2
echo "==============================================" >&2
test_model "deepseek/deepseek-flash" 2048 "复杂请求-deepseek-flash"
test_model "qwen3.8-27b" 2048 "复杂请求-qwen3.8-27b"

echo "==============================================" >&2
echo "测试完成！结果已保存到 api_benchmark_results.csv" >&2
echo "==============================================" >&2
cat api_benchmark_results.csv
