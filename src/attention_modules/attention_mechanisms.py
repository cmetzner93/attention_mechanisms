"""
This file contains source code contains the pytorch implementation for the different attention mechanisms. The scripts
are written to modularize the different variations.
    @author: Christoph Metzner
    @email: cmetzner@vols.utk.edu
    @created: 05/03/2022
    @last modified: 05/20/2022

Attention mechanisms:
    - Self-attention (implemented, tested)
    - Target-attention (implemented, tested)
    - Label-attention (implemented, tested)
    - Hierarchical-attention
        - Target attention (implemented, tested)
        - Label attention (implemented, tested)
    - Multi-head attention (implemented, tested; https://d2l.ai/chapter_attention-mechanisms/multihead-attention.html)
    - Alternating attention (implemented, tested)
"""
# built-in libraries
import os
import sys
from typing import Tuple, Union, List

# installed libraries
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

# custom libraries
from attention_modules.multihead_attention import transpose_output
from attention_modules.target_attention import TargetAttention
from attention_modules.label_attention import LabelAttention
from attention_modules.alternate_attention import AlternateAttention
from attention_modules.hierarchical_attention import HierarchicalTargetAttention, HierarchicalLabelAttention
from attention_modules.hierarchical_attention import HierarchicalContextAttention, HierarchicalDoubleAttention
from attention_modules.context_attention import ContextAttention, ContextAttentionDiffInput
from attention_modules.masked_attention import MaxMaskedAttention, RankedMaskedAttention

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
print(f'The experiment uses the following device: {device}', flush=True)


class Attention(nn.Module):
    """
    General attention class that initializes and performs selected attention mechanism.

    Parameters
    ----------
    num_labels : int
        Number of labels |L| in low-level label space
    embedding_dim : int
        Dimension of token embeddings
    latent_doc_dim : int
        Output dimension of encoder architecture, i.e., dimension of latent document representation
    att_module : str
        Selected attention mechanism
            target: Query matrix (Q) is randomly initialized
            label: Query matrix is initialized using sentence embedding of code descriptions of the label space
            self: Query matrix is the token input sequence itself
            alternate: Query matrix is randomly initialized (similar to target but with alternating attention heads)
            hierarchical_target: Query matrices are randomly initialized
            hierarchical_label: Query matrices are initialized using sentence embedding of code descriptions of all
                hierarchy levels
    scale : bool; default=False
        Flag indicating whether Energy Scores E (QxK.T) should be scaled using square-root(embedding_dim)
    multihead : bool; default=False
        Flag indicating if multihead attention has to be performed.
    num_heads : int; default=None
        Number of attention heads when performing multihead attention
    num_cats : int; default=None
        Number of categories |L| in high-level label space
    label_embedding_matrix : np.array; default=None
        Sentence embedding matrix of code descriptions of the low-level label space (e.g., billable ICD-9 codes)
        E.g., 003.0: Salmonella gastroenteritis
    cat_embedding_matrix : np.array; default=None
        Sentence embedding matrix of category descriptions of the high-level category label space (e.g., ICD-9 category)
        E.g., 001-139: Infectious And Parasitic Diseases
    code2cat_map: List[int]; default=None
        List containing a index mapping of the codes (lobels) to idx of categories

    """
    def __init__(self,
                 num_labels: int,
                 embedding_dim: int,
                 latent_doc_dim: int,
                 att_module: str,
                 scale: bool = False,
                 multihead: bool = False,
                 num_heads: int = None,
                 num_cats: int = None,
                 label_embedding_matrix: np.array = None,
                 cat_embedding_matrix: np.array = None,
                 code2cat_map: List[int] = None,
                 gamma: float = None):

        super().__init__()
        self._num_labels = num_labels
        self._embedding_dim = embedding_dim
        self._latent_doc_dim = latent_doc_dim
        self._att_module = att_module
        self._scale = scale
        self._multihead = multihead
        self._num_heads = num_heads
        self._num_cats = num_cats
        self._label_embedding_matrix = label_embedding_matrix
        self._cat_embedding_matrix = cat_embedding_matrix
        self._code2cat_map = code2cat_map
        self._gamma = gamma

        # Init multi-head attention output layer to concatenate output of all attention heads
        if self._multihead:
            self.MH_output = nn.Linear(in_features=self._latent_doc_dim,
                                       out_features=self._latent_doc_dim)

        if self._att_module == 'target':
            self.attention_layer = TargetAttention(num_labels=self._num_labels,
                                                   embedding_dim=self._embedding_dim,
                                                   latent_doc_dim=self._latent_doc_dim,
                                                   scale=self._scale,
                                                   multihead=self._multihead,
                                                   num_heads=self._num_heads)
            self.Q = self.attention_layer.Q.weight.clone()
        elif self._att_module == 'label':
            self.attention_layer = LabelAttention(num_labels=self._num_labels,
                                                  embedding_dim=self._embedding_dim,
                                                  latent_doc_dim=self._latent_doc_dim,
                                                  label_embedding_matrix=self._label_embedding_matrix,
                                                  scale=self._scale,
                                                  multihead=self._multihead,
                                                  num_heads=self._num_heads)
        elif self._att_module == 'alternate':
            self.attention_layer = AlternateAttention(num_labels=self._num_labels,
                                                      embedding_dim=self._embedding_dim,
                                                      latent_doc_dim=self._latent_doc_dim,
                                                      scale=self._scale,
                                                      multihead=self._multihead,
                                                      num_heads=self._num_heads)
        elif self._att_module == 'hierarchical_target':
            self.attention_layer = HierarchicalTargetAttention(num_labels=self._num_labels,
                                                               num_cats=self._num_cats,
                                                               embedding_dim=self._embedding_dim,
                                                               latent_doc_dim=self._latent_doc_dim,
                                                               code2cat_map=self._code2cat_map,
                                                               scale=self._scale,
                                                               multihead=self._multihead,
                                                               num_heads=self._num_heads)

        elif self._att_module == 'hierarchical_context':
            self.attention_layer = HierarchicalContextAttention(num_labels=self._num_labels,
                                                                num_cats=self._num_cats,
                                                                embedding_dim=self._embedding_dim,
                                                                latent_doc_dim=self._latent_doc_dim,
                                                                code2cat_map=self._code2cat_map,
                                                                scale=self._scale,
                                                                multihead=self._multihead,
                                                                num_heads=self._num_heads)

        elif self._att_module == 'hierarchical_double_attention':
            self.attention_layer = HierarchicalDoubleAttention(num_labels=self._num_labels,
                                                               num_cats=self._num_cats,
                                                               embedding_dim=self._embedding_dim,
                                                               latent_doc_dim=self._latent_doc_dim,
                                                               code2cat_map=self._code2cat_map,
                                                               scale=self._scale,
                                                               multihead=self._multihead,
                                                               num_heads=self._num_heads)

        elif self._att_module == 'hierarchical_label':
            self.attention_layer = HierarchicalLabelAttention(num_labels=self._num_labels,
                                                              num_cats=self._num_cats,
                                                              embedding_dim=self._embedding_dim,
                                                              latent_doc_dim=self._latent_doc_dim,
                                                              code2cat_map=self._code2cat_map,
                                                              cat_embedding_matrix=self._cat_embedding_matrix,
                                                              label_embedding_matrix=self._label_embedding_matrix,
                                                              scale=self._scale,
                                                              multihead=self._multihead,
                                                              num_heads=self._num_heads)
        elif self._att_module == 'context':
            self.attention_layer = ContextAttention(num_labels=self._num_labels,
                                                    embedding_dim=self._embedding_dim,
                                                    latent_doc_dim=self._latent_doc_dim,
                                                    scale=self._scale,
                                                    multihead=self._multihead,
                                                    num_heads=self._num_heads)

        elif self._att_module == 'context_diff':
            self.attention_layer = ContextAttentionDiffInput(num_labels=self._num_labels,
                                                             embedding_dim=self._embedding_dim,
                                                             latent_doc_dim=self._latent_doc_dim,
                                                             scale=self._scale,
                                                             multihead=self._multihead,
                                                             num_heads=self._num_heads)

        elif self._att_module == 'max_masked':
            self.attention_layer = MaxMaskedAttention(num_labels=self._num_labels,
                                                      embedding_dim=self._embedding_dim,
                                                      latent_doc_dim=self._latent_doc_dim,
                                                      gamma=self._gamma,
                                                      scale=self._scale,
                                                      multihead=self._multihead,
                                                      num_heads=self._num_heads)

        elif self._att_module == 'rank_masked':
            self.attention_layer = RankedMaskedAttention(num_labels=self._num_labels,
                                                         embedding_dim=self._embedding_dim,
                                                         latent_doc_dim=self._latent_doc_dim,
                                                         gamma=self._gamma,
                                                         scale=self._scale,
                                                         multihead=self._multihead,
                                                         num_heads=self._num_heads)

    def forward(self, H: torch.Tensor) -> Tuple[torch.Tensor]:
        """
        Forward pass of general attention mechanism class.

        Parameters
        ----------
        H : torch.Tensor  [batch_size, latent_doc_dim, sequence_length]
            Latent document representation after CNN, RNN, or Transformer

        Returns
        -------
        C : torch.Tensor
            Context matrix after attention mechanism
        A : torch.Tensor
            Attention weight matrix

        """
        # define Q
        if self._att_module == 'target':
            Q = self.Q.to(device)

        if self._multihead:
            C, A = self.attention_layer(H=H)
            C = transpose_output(X=C, num_heads=self._num_heads)
            A = transpose_output(X=A, num_heads=self._num_heads)
            C = self.MH_output(C)
        else:
            print(f'H.device: {H.device}')
            C, A = self.attention_layer(H=H, Q=Q)
        return C, A
