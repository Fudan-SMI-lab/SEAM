// =============================================================================
// Ascend C Custom Op - Host Side Template (Tiling + Op Definition)
// =============================================================================
// This file defines:
//   1. TilingData structure - parameters passed from host to device kernel
//   2. TilingFunc - computes tiling/partitioning strategy at graph compile time
//   3. OpDef class - declares inputs, outputs, attributes, and hardware config
//
// FILE NAMING: Save as op_host/{{op_name}}.cpp
//
// PLACEHOLDERS:
//   {{OP_NAME}}       - CamelCase op name (e.g., "MyCustomOp")
//   {{op_name}}       - snake_case op name (e.g., "my_custom_op")
//   {{SOC_VERSION}}   - Target SoC from the CANN build environment
//   {{TILING_FIELDS}} - TilingData field definitions
//   {{INPUTS}}        - Op input declarations
//   {{OUTPUTS}}       - Op output declarations
//   {{ATTRS}}         - Op attribute declarations
// =============================================================================

#include "register/tilingdata_base.h"
#include "tiling/platform/platform_ascendc.h"
#include "register/op_def_registry.h"

namespace optiling {

// ---------------------------------------------------------------------------
// TilingData: Host-to-device parameter structure
// ---------------------------------------------------------------------------
// Define all scalar parameters the kernel needs. These are serialized and
// passed to the device via the "tiling" GM_ADDR argument.
//
// Rules:
//   - Fields are commonly 4-byte aligned (pad fields can help)
//   - Supported types: uint32_t, int32_t, float, uint64_t, int64_t, double
//   - Total size should be small (fits in L2 cache line)
//
// Example fields:
//   TILING_DATA_FIELD_DEF(uint32_t, totalLength);
//   TILING_DATA_FIELD_DEF(uint32_t, tileNum);
//   TILING_DATA_FIELD_DEF(float, scaleFactor);
// ---------------------------------------------------------------------------
BEGIN_TILING_DATA_DEF({{OP_NAME}}TilingData)
    {{TILING_FIELDS}}
END_TILING_DATA_DEF;

REGISTER_TILING_DATA_CLASS({{OP_NAME}}, {{OP_NAME}}TilingData)

// ---------------------------------------------------------------------------
// TilingFunc: Called at graph compile time to compute kernel launch parameters
// ---------------------------------------------------------------------------
// Responsibilities:
//   - Read input shapes from context->GetInputShape()
//   - Read attributes from context->GetAttrs()
//   - Populate tiling data fields
//   - Set blockDim (number of AI Cores to use)
//   - Save tiling data to context buffer
//
// Multi-core strategy:
//   blockDim controls how many AI Cores run in parallel.
//   Each core gets a portion of the work (e.g., rows of a 2D tensor).
//   Inside the kernel, use GetBlockIdx() / GetBlockNum() to partition.
// ---------------------------------------------------------------------------
static ge::graphStatus TilingFunc(gert::TilingContext *context)
{
    {{OP_NAME}}TilingData tiling;

    // --- Extract input shape information ---
    // Example: auto shape = context->GetInputShape(0)->GetStorageShape();
    //          uint32_t totalLength = shape.GetDim(0) * shape.GetDim(1);
    auto shape = context->GetInputShape(0)->GetStorageShape();

    // --- Read op attributes (if any) ---
    // Example: float myAttr = *context->GetAttrs()->GetFloat(0);
    //          int32_t myInt = *context->GetAttrs()->GetInt(0);

    // --- Populate tiling fields ---
    // Example: tiling.set_totalLength(totalLength);

    // --- Set multi-core block dimension ---
    // blockDim = number of AI Cores to use (1 = single core)
    // For large tensors, split work across multiple cores within the target SoC limit.
    uint32_t blockDim = 1;
    context->SetBlockDim(blockDim);

    // --- Serialize tiling data ---
    tiling.SaveToBuffer(context->GetRawTilingData()->GetData(),
                        context->GetRawTilingData()->GetCapacity());
    context->GetRawTilingData()->SetDataSize(tiling.GetDataSize());

    // --- Workspace (set to 0 if not needed) ---
    size_t *currentWorkspace = context->GetWorkspaceSizes(1);
    currentWorkspace[0] = 0;

    return ge::GRAPH_SUCCESS;
}
}

// ---------------------------------------------------------------------------
// OpDef: Operator interface definition
// ---------------------------------------------------------------------------
// Declares the op's inputs, outputs, attributes, and hardware configuration.
// This is how the CANN runtime knows the op's signature.
//
// Input/Output options:
//   .ParamType({{PARAM_TYPE}})     - input parameter category
//   .ParamType(OPTIONAL)           - may be nullptr
//   .DataType({ge::DT_FLOAT})      - supported: DT_FLOAT, DT_FLOAT16, DT_INT32, etc.
//   .Format({ge::FORMAT_ND})       - ND = arbitrary dimensions
//
// Attribute types:
//   .Float(default)   .Int(default)   .Bool(default)   .String(default)
//   .ListFloat()      .ListInt()
//
// Hardware config:
//   .AICore().SetTiling(optiling::TilingFunc)  - register tiling function
//   .AICore().AddConfig("{{SOC_VERSION}}")     - target SoC
// ---------------------------------------------------------------------------
namespace ops {
class {{OP_NAME}} : public OpDef {
public:
    explicit {{OP_NAME}}(const char *name) : OpDef(name)
    {
        // --- Inputs ---
        {{INPUTS}}

        // --- Attributes (optional scalar parameters) ---
        {{ATTRS}}

        // --- Outputs ---
        {{OUTPUTS}}

        // --- Hardware configuration ---
        this->AICore()
            .SetTiling(optiling::TilingFunc);
        this->AICore().AddConfig("{{SOC_VERSION}}");
    }
};

OP_ADD({{OP_NAME}});
}
