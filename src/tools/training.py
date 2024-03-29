"""
This file contains source code for the training procedure of the models.
    @author: Christoph Metzner
    @email: cmetzner@vols.utk.edu
    @created: 05/03/2022
    @last modified: 03/07/2022
"""

# built-in libraries
import os
import time
from typing import Dict, Union, List
import h5py

# installed libraries
import torch
import torch.nn as nn
import numpy as np

# Custom libraries
from .performance_metrics import get_scores

# Select GPU as hardware if available otherwise use available CPU
device = ('cuda' if torch.cuda.is_available() else 'cpu')


def train(model: nn.Module,
          train_kwargs: Dict[str, Union[bool, int]],
          optimizer,
          train_loader,
          epoch: int = 0,
          transformer: bool = False,
          val_loader=None,
          scheduler=None,
          save_name: str = None,
          return_en_scores: bool = False,
          att_module: str=None):
    """
    This function handles training and validating the model using the given training and validation datasets.

    Parameters
    ----------
    model : nn.Model
        Multi-label or multi-class classification model implemented in pytorch using nn.Model
    train_kwargs : Dict[str, Union[bool, int]]
        Dictionary storing all variables required to run training/validating process.
    optimizer : pytorch optimizer
        Optimizer used for controlling parameter training via backward propagation
    train_loader : pytorch data loader
        Dataloader containing the training dataset; samples X and ground-truth values Y
    transformer : bool; default=False
        Flag indicating whether the model is a transformer or not
    val_loader : pytorch data loader
        Dataloader contianing the validation dataset; samples X and ground-truth values Y
    scheduler : pytorch learning rate scheduler
        Learning rate scheduler
    save_name : str; default=None
        Descriptive name to save the trained model

    """

    epochs = train_kwargs['epochs']
    patience = train_kwargs['patience']

    # https://pytorch.org/docs/stable/generated/torch.nn.BCEWithLogitsLoss.html
    # Multilabel requires using the sigmoid function to compute pseudo-probabilities ranging [0, 1] for each label
    # Use BCEWithLogitsLoss() for increased numerical stability
    loss_fct = torch.nn.BCEWithLogitsLoss()

    # Variables to track validation performance and early stopping
    best_val_loss = np.inf
    patience_counter = 0

    ### Train model ###
    for epoch in range(epoch, epochs):
        print(f'Epoch: {epoch + 1}', flush=True)
        # Enable training of layers with trainable parameters
        model.train()

        # Keep track of training time
        start_time = time.time()
        for b, batch in enumerate(train_loader):
            ## if-statement for debugging the code
            #if b == 1:
            #    break
            # set gradients to zero for every new batch
            optimizer.zero_grad()

            # Compute logits and return attention/energy scores if prompted
            if transformer:
                X = batch['input_ids'].to(device)
                Y = batch['labels'].to(device)
                if return_en_scores:
                    logits, E = model(input_ids=X, return_en_scores=return_en_scores)
                else:
                    logits = model(input_ids=X, return_en_scores=False)
            else:
                X = batch['X'].to(device)
                Y = batch['Y'].to(device)
                if return_en_scores:
                    logits, E = model(X, return_en_scores)
                else:
                    logits = model(X)

            # Compute loss
            loss = 0
            loss += loss_fct(logits, Y)

            # perform backpropagation
            loss.backward()
            optimizer.step()
            l_cpu = loss.cpu().detach().numpy()

        scheduler.step()
        print(f'Training loss: {l_cpu} ({time.time() - start_time:.2f} sec)', flush=True)

        # save checkpoint for current model
        if epoch % 2 == 0:
            save_name_check = f'{save_name}_checkpoint.pt'
            torch.save({'epoch': epoch,
                        'model_state_dict': model.state_dict(),
                        'optimizer_state_dict': optimizer.state_dict(),
                        'loss': l_cpu},
                       save_name_check)

        ### Validate model ###
        if val_loader is not None:
            scores = scoring(model=model,
                             data_loader=val_loader,
                             att_module=att_module,
                             transformer=transformer,
                             quartiles_indices=None,
                             individual=False)
            val_loss = scores['loss']
            scores['final_epoch'] = epoch

            ### Early stopping to prevent overfitting ###
            if val_loss < best_val_loss:
                best_val_loss = val_loss
                patience_counter = 0
                torch.save(model.state_dict(), f'{save_name}.pt')
            else:
                patience_counter += 1
                print(f'Patience: {patience_counter}')
                if patience_counter >= patience:
                    os.remove(f'{save_name}_checkpoint.pt')
                    break

    # If training reaches final epoch store model state dict and remove last checkpoint
    if epoch + 1 == epochs:
        torch.save(model.state_dict(), f'{save_name}.pt')
        try:
            os.remove(f'{save_name}_checkpoint.pt')
        except FileNotFoundError:
            pass

    epoch = epoch + 1
    return epoch


def scoring(model,
            data_loader,
            att_module,
            transformer: bool = False,
            quartiles_indices: List[int] = None,
            individual: bool = False,
            return_en_scores: bool = False,
            path_scores: str = None) -> Dict[str, Union[float, np.array]]:

    """
    Parameters
    ----------

    model : nn.Model
        Multi-label or multi-class classification model implemented in pytorch using nn.Model
    data_loader : pytorch data loader
        Dataloader containing the training dataset; samples X and ground-truth values Y
    transformer : bool; default=False
        Flag indicating if model is a transformer or not.
    quartiles_indices : List[int]; default=None
        List containing information in which quartile a respective label is
    individual : bool; default=False
        Flag indicating if performance metrics should be computed for each label in the label space individually
    return_en_scores : bool; default=False
        Flag indicating if attention and energy scores should be retrieved

    Returns
    -------
    Dict[str, Union[float, np.array]]
        Dictionary containing the computed performance metrics (scores) and ground-truth values, prediction probabilites
        prediction, and validating/testing loss.
    """
    # Multilabel requires using the sigmoid function to compute pseudo-probabilities ranging [0, 1] for each label
    # Use BCEWithLogitsLoss() for increased numerical stability
    loss_fct = torch.nn.BCEWithLogitsLoss()

    # Put model in evaluation mode; turns off stochastic based layers (e.g., dropout or batch normalization)
    model.eval()

    # Init arrays to keep track of ground-truth labels and predictions
    y_trues = []
    y_probs = []
    y_preds = []

    # Init list to keep track of losses per batch and running validation loss variable
    losses = []
    if return_en_scores:
        path_en = path_scores + '_en_scores'
    # switch off autograd engine; reduces memory usage and increase computation speed
    with torch.no_grad():
        # loop through dataset
        for b, batch in enumerate(data_loader):
            # if statement for debugging the code
            #if b == 1:
            #    break
            if transformer:
                X = batch['input_ids'].to(device)
                Y = batch['labels'].to(device)
                if return_en_scores:
                    logits, E = model(input_ids=X, return_en_scores=return_en_scores)
                    # Store energy scores per batch to hdf5 files
                    path_en_b = path_en + f'_batch{b}'  # add batch identifier
                    with h5py.File(path_en_b + '.hdf5', 'w') as f:
                        df = f.create_dataset("scores", data=E.detach().cpu().numpy(), dtype='e',
                                              compression="gzip")
                    del df
                else:
                    logits = model(input_ids=X, return_en_scores=False)
            else:
                # Retrieve token2id mapped and 0-padded documents of current batch
                X = batch['X'].to(device)
                Y = batch['Y'].to(device)
                if return_en_scores:
                    logits, E = model(X, return_en_scores)

                    # Store attention and energy scores in batches
                    path_en_b = path_en + f'_batch{b}'  # add batch identifier
                    with h5py.File(path_en_b + '.hdf5', 'w') as f:
                        df = f.create_dataset("scores", data=E.detach().cpu().numpy(), dtype='e',
                                              compression="gzip")
                    del df
                else:
                    logits = model(X)

            loss = 0

            # Extend arrays with ground-truth values (Y), prediction probabilities (probs), and predictions (logits)
            y_trues.extend(Y.detach().cpu().numpy())
            probs = torch.sigmoid(logits).detach().cpu().numpy()
            y_probs.extend(probs)
            y_preds.extend(np.round(probs, 0))

            # Compute the loss for current batch
            loss += loss_fct(logits, Y)
            l_cpu = loss.cpu().detach().numpy()
            losses.append(l_cpu)

    # Compute the scores
    scores = {}
    scores, y_preds_, y_trues_, y_probs_ = get_scores(y_preds_=y_preds,
                                                      y_trues_=y_trues,
                                                      y_probs_=y_probs,
                                                      scores=scores,
                                                      ks=[5, 8, 15],
                                                      quartiles_indices=quartiles_indices,
                                                      individual=individual)

    loss = np.mean(losses)

    scores['y_trues'] = y_trues_
    scores['y_probs'] = y_probs_
    scores['y_preds'] = y_preds_
    scores['loss'] = loss

    return scores
