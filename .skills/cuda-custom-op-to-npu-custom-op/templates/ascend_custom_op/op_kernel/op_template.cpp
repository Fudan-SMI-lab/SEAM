// =============================================================================
// Ascend C Custom Op - Kernel Template (Device Side)
// =============================================================================
// This file implements the Ascend C kernel that runs on AI Core(s).
//
// FILE NAMING: Save as op_kernel/{{op_name_lower}}.cpp
//   The kernel file name matches the function name in the extern "C" entry.
//
// PATTERN: The standard Ascend C kernel follows this structure:
//   1. Kernel class with Init(), Process(), CopyIn(), Compute(), CopyOut()
//   2. extern "C" entry point that unpacks tiling data and calls the kernel
//
// PLACEHOLDERS:
//   {{OP_NAME}}        - CamelCase op name (e.g., "MyCustomOp")
//   {{op_name_lower}}  - lowercase kernel function name (e.g., "my_custom_op")
//   {{KERNEL_PARAMS}}  - Parameters for the kernel entry function
//   {{KERNEL_BODY}}    - Compute logic inside the Compute() method
//
// KEY CONCEPTS:
//   - GM_ADDR: Global Memory address (device DRAM pointer)
//   - GlobalTensor: View into global memory
//   - LocalTensor: On-chip buffer (UB - Unified Buffer)
//   - TPipe: Pipeline manager for double-buffering
//   - TQue<VECIN>: Input queue (GM -> UB)
//   - TQue<VECOUT>: Output queue (UB -> GM)
//   - DataCopy: DMA transfer between GM and UB
//   - BUFFER_NUM=2: Double buffering for pipeline overlap
// =============================================================================

#include "kernel_operator.h"
using namespace AscendC;

constexpr int32_t BUFFER_NUM = 2;

class Kernel{{OP_NAME}} {
public:
    __aicore__ inline Kernel{{OP_NAME}}() {}

    // -------------------------------------------------------------------------
    // Init: Set up global memory views and allocate on-chip buffers
    // -------------------------------------------------------------------------
    // Responsibilities:
    //   - Store tiling parameters as member variables
    //   - Compute per-core work partition using GetBlockIdx()/GetBlockNum()
    //   - SetGlobalBuffer for each input/output tensor
    //   - InitBuffer for input/output queues (size = elements * sizeof(dtype))
    // -------------------------------------------------------------------------
    __aicore__ inline void Init(
        {{KERNEL_PARAMS}},
        GM_ADDR workspace, GM_ADDR tiling)
    {
        // --- Multi-core work partitioning ---
        ASSERT(GetBlockNum() != 0 && "block dim cannot be zero!");
        uint32_t blockIdx = GetBlockIdx();
        uint32_t blockDim = GetBlockNum();

        // Example: partition totalLength across cores
        // uint32_t elementsPerCore = (totalLength + blockDim - 1) / blockDim;
        // uint32_t startOffset = blockIdx * elementsPerCore;
        // uint32_t endOffset = min(startOffset + elementsPerCore, totalLength);
        // this->processCount = endOffset - startOffset;

        // --- Set up global memory tensor views ---
        // Example:
        // inputGm.SetGlobalBuffer((__gm__ float *)input_gm + startOffset, processCount);
        // outputGm.SetGlobalBuffer((__gm__ float *)output_gm + startOffset, processCount);

        // --- Allocate on-chip buffers ---
        // Buffer size is sized for the data copied in each tile iteration.
        // Example for processing tileLength elements per iteration:
        // uint32_t tileBytes = tileLength * sizeof(float);
        // pipe.InitBuffer(inQueue, BUFFER_NUM, tileBytes);
        // pipe.InitBuffer(outQueue, BUFFER_NUM, tileBytes);
    }

    // -------------------------------------------------------------------------
    // Process: Main loop that orchestrates CopyIn -> Compute -> CopyOut
    // -------------------------------------------------------------------------
    // This drives the pipeline. Each iteration processes one "tile" of data.
    // The double-buffering (BUFFER_NUM=2) allows overlap:
    //   While tile N is being computed, tile N+1 can be copied in.
    // -------------------------------------------------------------------------
    __aicore__ inline void Process()
    {
        // Example: iterate over tiles
        // int32_t tileCount = (this->processCount + tileLength - 1) / tileLength;
        // for (int32_t i = 0; i < tileCount; i++) {
        //     CopyIn(i);
        //     Compute(i);
        //     CopyOut(i);
        // }
    }

private:
    // -------------------------------------------------------------------------
    // CopyIn: DMA transfer from Global Memory to on-chip buffer (UB)
    // -------------------------------------------------------------------------
    // Steps:
    //   1. AllocTensor from inQueue (gets a free buffer slot)
    //   2. DataCopy from GlobalTensor to LocalTensor
    //   3. EnQue the filled buffer to make it available for Compute
    // -------------------------------------------------------------------------
    __aicore__ inline void CopyIn(int32_t tileIdx)
    {
        LocalTensor<float> inputLocal = inQueue.AllocTensor<float>();

        // Example: copy one tile from global memory
        // uint32_t offset = tileIdx * tileLength;
        // uint32_t copyLen = min(tileLength, processCount - offset);
        // DataCopy(inputLocal, inputGm[offset], copyLen);

        inQueue.EnQue(inputLocal);
    }

    // -------------------------------------------------------------------------
    // Compute: Perform the actual computation on on-chip data
    // -------------------------------------------------------------------------
    // Steps:
    //   1. DeQue from inQueue (get filled input buffer)
    //   2. AllocTensor from outQueue (get empty output buffer)
    //   3. Perform computation (vector ops or scalar element access)
    //   4. EnQue result to outQueue
    //   5. FreeTensor the input buffer
    //
    // Ascend C vector intrinsics (preferred for performance):
    //   Add(dst, src1, src2, count)    - element-wise add
    //   Mul(dst, src1, src2, count)    - element-wise multiply
    //   Muls(dst, src, scalar, count)  - scalar multiply
    //   Adds(dst, src, scalar, count)  - scalar add
    //   Exp(dst, src, count)           - element-wise exp
    //   Relu(dst, src, count)          - element-wise ReLU
    //
    // Scalar element access (for complex stencil patterns):
    //   float val = src.GetValue(index);
    //   dst.SetValue(index, val);
    // -------------------------------------------------------------------------
    __aicore__ inline void Compute(int32_t tileIdx)
    {
        LocalTensor<float> inputLocal = inQueue.DeQue<float>();
        LocalTensor<float> outputLocal = outQueue.AllocTensor<float>();

        {{KERNEL_BODY}}

        outQueue.EnQue<float>(outputLocal);
        inQueue.FreeTensor(inputLocal);
    }

    // -------------------------------------------------------------------------
    // CopyOut: DMA transfer from on-chip buffer back to Global Memory
    // -------------------------------------------------------------------------
    __aicore__ inline void CopyOut(int32_t tileIdx)
    {
        LocalTensor<float> outputLocal = outQueue.DeQue<float>();

        // Example: copy result back to global memory
        // uint32_t offset = tileIdx * tileLength;
        // uint32_t copyLen = min(tileLength, processCount - offset);
        // DataCopy(outputGm[offset], outputLocal, copyLen);

        outQueue.FreeTensor(outputLocal);
    }

private:
    TPipe pipe;
    TQue<QuePosition::VECIN, BUFFER_NUM> inQueue;
    TQue<QuePosition::VECOUT, BUFFER_NUM> outQueue;
    // GlobalTensor<float> inputGm, outputGm;
    // Add member variables for tiling params and per-core state
};

// =============================================================================
// Kernel Entry Point
// =============================================================================
// This is the function called by the CANN runtime. Its name matches the
// kernel file name (without .cpp extension).
//
// Parameters:
//   - One GM_ADDR per input tensor (in order matching OpDef inputs)
//   - One GM_ADDR per output tensor (in order matching OpDef outputs)
//   - GM_ADDR workspace (runtime-allocated scratch space, can be unused)
//   - GM_ADDR tiling (serialized TilingData from host TilingFunc)
//
// GET_TILING_DATA macro deserializes the tiling buffer into a typed struct.
// =============================================================================
extern "C" __global__ __aicore__ void {{op_name_lower}}(
    {{KERNEL_PARAMS}},
    GM_ADDR workspace, GM_ADDR tiling)
{
    GET_TILING_DATA(tiling_data, tiling);
    Kernel{{OP_NAME}} op;
    op.Init({{KERNEL_PARAMS}}, workspace, tiling);
    op.Process();
}
