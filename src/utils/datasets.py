import torch, h5py
from torch.utils.data import Dataset

class H5Dataset(Dataset):
    def __init__(self, h5_file_path, indices):
        self.h5_file_path = h5_file_path
        self.indices = indices
        self.h5_file = None

        with h5py.File(self.h5_file_path, 'r') as f:
            self.input_shape = f['inputs'].shape[1:]
            self.policy_shape = f['policy_targets'].shape[1:] if len(f['policy_targets'].shape) > 1 else ()
            self.value_shape = f['value_targets'].shape[1:] if len(f['value_targets'].shape) > 1 else ()

    def __len__(self):
        return len(self.indices)

    def __getitem__(self, idx):
        if self.h5_file is None:
            self.h5_file = h5py.File(self.h5_file_path, 'r')

        try:
            actual_idx = self.indices[idx]
            inp = self.h5_file['inputs'][actual_idx]
            pol = self.h5_file['policy_targets'][actual_idx]
            val = self.h5_file['value_targets'][actual_idx]

            if inp.shape != self.input_shape:
                raise ValueError(f"Input shape mismatch at index {actual_idx}")
            if pol.shape != self.policy_shape:
                raise ValueError(f"Policy target shape mismatch at index {actual_idx}")
            if val.shape != self.value_shape:
                raise ValueError(f"Value target shape mismatch at index {actual_idx}")

            inp_t = torch.from_numpy(inp).float()
            pol_t = torch.tensor(pol).long()
            val_t = torch.tensor(val).float()
            return inp_t, pol_t, val_t
        except Exception as e:
            raise RuntimeError(f"Error loading data at index {idx}: {str(e)}")

    def __del__(self):
        if self.h5_file is not None:
            self.h5_file.close()