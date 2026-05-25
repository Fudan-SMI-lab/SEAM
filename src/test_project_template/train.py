import os
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset


class SimpleMLP(nn.Module):
    def __init__(self, input_dim=784, hidden_dim=256, output_dim=10):
        super().__init__()
        self.fc1 = nn.Linear(input_dim, hidden_dim)
        self.relu = nn.ReLU()
        self.fc2 = nn.Linear(hidden_dim, output_dim)

    def forward(self, x):
        x = x.view(x.size(0), -1)
        x = self.relu(self.fc1(x))
        x = self.fc2(x)
        return x


def main():
    # [E2E_TEST_INJECTION] Simulate a CUDA-specific import that breaks on NPU
    # The repair agent should remove this line after analyzing the error
    from apex_C import fused_layer_norm  # noqa: F401 - intentionally broken for E2E test

    # Device setup with CUDA string literals
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Using device: {device}")

    # Distributed backend (NCCL)
    if torch.cuda.device_count() > 1:
        torch.distributed.init_process_group(backend="nccl")

    # Model and data
    model = SimpleMLP().to(device)

    X_train = torch.randn(512, 784)
    y_train = torch.randint(0, 10, (512,))
    dataset = TensorDataset(X_train.to(device), y_train.to(device))
    loader = DataLoader(dataset, batch_size=64, shuffle=True)

    optimizer = optim.Adam(model.parameters(), lr=1e-3)
    criterion = nn.CrossEntropyLoss()

    # Training loop with AMP
    epochs = 2
    for epoch in range(epochs):
        model.train()
        total_loss = 0.0
        for batch_x, batch_y in loader:
            batch_x = batch_x.to(device)
            batch_y = batch_y.to(device)

            with torch.cuda.amp.autocast():
                output = model(batch_x)
                loss = criterion(output, batch_y)

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            total_loss += loss.item()

        print(f"Epoch {epoch + 1}/{epochs}, Loss: {total_loss / len(loader):.4f}")

    print("Training complete.")


if __name__ == "__main__":
    main()
