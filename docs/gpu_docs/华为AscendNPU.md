# 华为昇腾 Ascend NPU

昇腾（Ascend）是华为面向全场景 AI 计算打造的端边云协同全栈软硬件生态体系，核心为自研达芬奇（Da Vinci）架构 NPU 芯片，对标英伟达 GPU，用于AI训练、推理与大模型算力底座。

狭义 Ascend 指 NPU 芯片；广义则包含：
*   昇腾 NPU 芯片（ 910 /  950 等）
*   Atlas 硬件（模组、板卡、服务器、集群）
*   CANN 异构计算架构
*   MindSpore 深度学习框架
*   MindX 应用使能套件
    

## SEAM中说的华为昇腾

当前昇腾演进后，CUDA代码迁移量降低。关于Ascend 910 / 950 系列具体参数如下（训练/推理）

*   Ascend 910A：FP16 峰值280TFLOPS，功耗约 310–350W
*   Ascend 910B：FP16 ≥280 TFLOPS
*   Ascend 910C：双芯封装，FP16 ≥560 TFLOPS，提升单机算力密度
*   Ascend 950：4代，支持 FP8/FP4，主打大模型训练成本优化
    
对应的硬件，大家会听到 Atlas 800、Atlas 900 、Atlas 384 超节点等。

## 开发和适配工作资料查询地

注意：华为昇腾早期仓库在gitee，后来迁移到了gitcode。更新很频繁，请各位同仁去gitcode上查询。如果有问题，华为的工程师也说git上提issue会解决得更快。

* 昇腾Ascend开源软件仓库：https://gitcode.com/Ascend

* CANN开源仓库：https://gitcode.com/cann
    
* 昇腾官网：https://www.hiascend.com
    
* 昇腾文档中心：https://www.hiascend.com/document ，关注上面的导航栏。
    - 比如：华为昇腾Ascend C算子开发文档：https://www.hiascend.com/document/detail/zh/canncommercial/900/programug/Ascendcopdevg/atlas\_ascendc\_map\_10\_0002.htmlatlas_ascendc_map_10_0002.html
        
    - 直接开箱能用的Docker镜像仓库：https://www.hiascend.com/developer/ascendhub