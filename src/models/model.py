import torch, torch.nn as nn, src.utils.chess_utils as chess_utils
from typing import Tuple, Optional


class SELayer(nn.Module):
    """Squeeze-and-Excitation layer for channel-wise attention.
    
    Args:
        channel: Number of input channels
        reduction: Reduction ratio for the bottleneck (default: 16)
    """
    
    def __init__(self, channel: int, reduction: int = 16) -> None:
        super().__init__()
        
        # Calculate bottleneck size with minimum of 1
        bottleneck_size = max(channel // reduction, 1)
        
        self.avg_pool = nn.AdaptiveAvgPool2d(1)
        self.fc = nn.Sequential(
            nn.Linear(channel, bottleneck_size, bias=False),
            nn.ReLU(inplace=True),
            nn.Linear(bottleneck_size, channel, bias=False),
            nn.Sigmoid()
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        batch_size, channels, _, _ = x.size()
        y = self.avg_pool(x).view(batch_size, channels)
        y = self.fc(y).view(batch_size, channels, 1, 1)
        
        if self.training:
            return x.mul_(y.expand_as(x))
        return x * y.expand_as(x)


class SEResidualUnit(nn.Module):
    """Squeeze-and-Excitation Residual Unit combining SE attention with residual connections.
    
    Args:
        in_channels: Number of input channels
        out_channels: Number of output channels
        stride: Stride for convolutions (default: 1)
        reduction: Reduction ratio for SE layer (default: 16)
    """
    
    def __init__(
        self, 
        in_channels: int, 
        out_channels: int, 
        stride: int = 1, 
        reduction: int = 16
    ) -> None:
        super().__init__()
        
        self.conv_block = nn.Sequential(
            # Bottleneck path
            nn.Conv2d(in_channels, out_channels // 4, kernel_size=1, stride=stride, bias=False),
            nn.BatchNorm2d(out_channels // 4),
            nn.ReLU(inplace=True),
            
            nn.Conv2d(out_channels // 4, out_channels // 4, kernel_size=3, padding=1, bias=False),
            nn.BatchNorm2d(out_channels // 4),
            nn.ReLU(inplace=True),
            
            nn.Conv2d(out_channels // 4, out_channels, kernel_size=1, bias=False),
            nn.BatchNorm2d(out_channels)
        )
        
        self.se = SELayer(out_channels, reduction)
        self.relu = nn.ReLU(inplace=True)
        
        # Optional downsampling for residual connection
        self.downsample = None
        if stride != 1 or in_channels != out_channels:
            self.downsample = nn.Sequential(
                nn.Conv2d(in_channels, out_channels, kernel_size=1, stride=stride, bias=False),
                nn.BatchNorm2d(out_channels)
            )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        identity = x
        
        # Main path
        out = self.conv_block(x)
        out = self.se(out)
        
        # Residual connection
        if self.downsample is not None:
            identity = self.downsample(x)
        out += identity
        
        return self.relu(out)


class ChessModel(nn.Module):
    """Neural network model for chess position evaluation and move prediction.
    
    Args:
        filters: Number of filters in convolutional layers (default: 64)
        res_blocks: Number of residual blocks (default: 10)
        num_moves: Number of possible moves to predict (default: chess_utils.TOTAL_MOVES)
    """
    
    def __init__(
        self, 
        filters: int = 64, 
        res_blocks: int = 10, 
        num_moves: Optional[int] = None
    ) -> None:
        super().__init__()
        
        self.num_moves = num_moves if num_moves is not None else chess_utils.TOTAL_MOVES
        
        # Initial convolution block
        self.initial_block = nn.Sequential(
            nn.Conv2d(20, filters, kernel_size=3, padding=1, bias=False),
            nn.BatchNorm2d(filters),
            nn.ReLU(inplace=True)
        )
        
        # Residual blocks
        self.residual_layers = nn.Sequential(
            *[SEResidualUnit(filters, filters) for _ in range(res_blocks)]
        )
        
        # Policy head
        self.policy_head = nn.Sequential(
            nn.Conv2d(filters, 2, kernel_size=1, bias=False),
            nn.BatchNorm2d(2),
            nn.ReLU(inplace=True)
        )
        self.policy_fc = nn.Linear(2 * 8 * 8, self.num_moves)
        
        # Value head
        self.value_head = nn.Sequential(
            nn.Conv2d(filters, 1, kernel_size=1, bias=False),
            nn.BatchNorm2d(1),
            nn.ReLU(inplace=True)
        )
        self.value_fc = nn.Sequential(
            nn.Linear(1 * 8 * 8, 128),
            nn.BatchNorm1d(128),
            nn.ReLU(inplace=True),
            nn.Linear(128, 1)
        )
        
        self._initialize_weights()

    def _initialize_weights(self) -> None:
        """Initialize model weights using standard initialization techniques."""
        for module in self.modules():
            if isinstance(module, nn.Conv2d):
                nn.init.kaiming_normal_(module.weight, mode='fan_out', nonlinearity='relu')
            elif isinstance(module, nn.BatchNorm2d):
                nn.init.constant_(module.weight, 1)
                nn.init.constant_(module.bias, 0)
            elif isinstance(module, nn.Linear):
                nn.init.normal_(module.weight, 0, 0.01)
                if module.bias is not None:
                    nn.init.constant_(module.bias, 0)

    def forward(self, x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """Forward pass of the model.
        
        Args:
            x: Input tensor of shape (batch_size, 20, 8, 8)
            
        Returns:
            Tuple of (policy_output, value_output):
                - policy_output: Move probabilities
                - value_output: Position evaluation in range [-1, 1]
        """
        # Common layers
        x = self.initial_block(x)
        x = self.residual_layers(x)
        
        # Policy head
        p = self.policy_head(x)
        p = p.view(p.size(0), -1)
        policy_output = self.policy_fc(p)
        
        # Value head
        v = self.value_head(x)
        v = v.view(v.size(0), -1)
        value_output = torch.tanh(self.value_fc(v))
        
        return policy_output, value_output