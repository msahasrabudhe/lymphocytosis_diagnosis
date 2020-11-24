"""
Training script for lymphocytosis diagnosis
"""

# ===============================================================================================================================
#   Model imports. 
#   Also imports other things. See model.py for details.
from    model               import  *
import  torchvision.utils   as      vutils

# ===============================================================================================================================
#   Logging
from    tensorboardX        import  SummaryWriter

# ===============================================================================================================================
#   Command line arguments. Only used to specify config file,
#   output directory, and GPU ID. 
import  argparse

# ===============================================================================================================================
#   Global variables
SICK_TARGET                                     = 1
HEALTHY_TARGET                                  = 1 - SICK_TARGET

NOISY_POS_TARGET                                =  1.3169578969248166       # log(2 + sqrt(3))
NOISY_NEG_TARGET                                = -1.3169578969248164       # log(2 - sqrt(3))

EPOCHS_LR_CRITERION                             = 10
REDUCE_LR_CRITERION                             = 0.01

NORM_FACTOR                                     = 0.3989422804014327        # 1 / sqrt(2 * pi)

# ===============================================================================================================================
def main(cmd_args=None):
    """
    The main function to run training. 
    """

    _reduce_lr_criterion                        = REDUCE_LR_CRITERION
    _epochs_lr_criterion                        = EPOCHS_LR_CRITERION

    parser                                      = argparse.ArgumentParser()
    parser.add_argument('--cfg',        default=None,       type=str,   help='Path to config file.')
    parser.add_argument('--gpu_id',     default=0,          type=int,   help='GPU to use.')
    parser.add_argument('--output_dir', default='output/',  type=str,   help='Root output directory.')
    if cmd_args is not None:
        args                                    = parser.parse_args(cmd_args.split(' '))
    else:
        args                                    = parser.parse_args()

    # ===========================================================================================================================
    #   Configuration file must exist.
    if args.cfg is not None and not os.path.exists(args.cfg):
        raise ValueError(cfm('HALT', 'r', 'b') + ': specified configuration file [%s] does not exist!' %(args.cfg))

    # ===========================================================================================================================
    #   Load options. 
    options                                     = load_yaml(args.cfg)
    #   Make options backward compatible. 
    options                                     = fix_backward_compatibility(options)


    # ===========================================================================================================================
    #   Extract experiment name and other values from cfg.
    experiment_name                             = os.path.split(args.cfg)[-1].replace('.yaml', '')
    options.experiment_name                     = experiment_name
    if not hasattr(options, 'output_dir'):
        options.output_dir                      = os.path.join(args.output_dir, experiment_name)
    print(cfm('INFO', 'y', 'b') + ': Using configuration file [%s]. Experiment name is [%s]. ' %(args.cfg, experiment_name))

    # ===========================================================================================================================
    #   Check whether we are resuming this experiment
    if os.path.exists(options.output_dir) and os.path.exists(os.path.join(options.output_dir, 'system_state.pkl')):
        # Only resume if there is at least one epoch that has passed (at least something was saved).
        resuming                                = True
        # Set load_from to output_dir to load previous checkpoints. 
        options.training.load_from              = options.output_dir
        options.training.load_mlp_from          = False
        options.training.load_cnn_from          = False
        write_flush(cfm('INFO', 'y', 'b') + ': %s already exists. ' %(options.output_dir))
        write_flush('I will attempt to resume previous training session.\n')
        with open(os.path.join(options.output_dir, 'system_state.pkl'), 'rb') as fp:
            system_state                        = pickle.load(fp)
    
        iter_mark                               = system_state['iter_mark']
        tr_epoch                                = system_state['tr_epoch']
        train_val_losses                        = system_state['train_val_losses']
        print(cfm('INFO', 'y', 'b') + ': System state loaded from %s.' %(os.path.join(options.output_dir, 'system_state.pkl')))
    else:
        iter_mark                               = 0
        tr_epoch                                = 0
        train_val_losses                        = []
        print(cfm('INFO', 'y', 'b') + ': Starting from an uninitialised model.')
        if not os.path.exists(options.output_dir):
            os.makedirs(options.output_dir)

    for sub_dir in ['best_model/', 'val_pred/', 'test_pred/']:
        if not os.path.exists(os.path.join(options.output_dir, sub_dir)):
            os.makedirs(os.path.join(options.output_dir, sub_dir))

        
    current_lr                                  = options.training.lr

    # ===========================================================================================================================
    #   Whether to use the GPU.
    options.training.cuda                       = (args.gpu_id != -1)
    if options.training.cuda:
        #   Set GPU.
        torch.cuda.set_device(args.gpu_id)

    # ===========================================================================================================================
    #   Logging
    options.training.log_dir                    = os.path.join(options.training.log_dir, experiment_name)
    if options.training.do_logging:
        writer                                  = SummaryWriter(options.training.log_dir, purge_step=tr_epoch)

    # ===========================================================================================================================
    #   Initialise a model!
    model                                       = Model(options)
    print(cfm('INFO', 'y', 'b') + ': Model initialised!')
    print(model)
    sys.stdout.flush()
    if options.training.cuda:
        model                                   = model.cuda()

    # ===========================================================================================================================
    #   If resuming, load from previous model. 
    # If load_from is set, we should import the previous model. 
    if options.training.load_from:
        write_flush('Loading %s models from previously saved training session at %s ...\n' %(options.training.load_which, options.training.load_from))
        model.load_checkpoint(options.training.load_from, which=options.training.load_which)

        # Begin: hack
#        if options.training.freeze_cnn:
#            model.cnn.agg_linear.weight.requires_grad = False
#            model.cnn.agg_linear.weight.normal_(0, 0.01)
#            model.cnn.agg_linear.weight.requires_grad = True
        # END: hack
        write_flush('\n')
    else:
        if options.training.load_cnn:
            write_flush(cfm('INFO', 'y', 'b') + ': Loading pre-trained CNN from %s ...\n' %(options.training.load_cnn))
            model.load_submodel(options.training.load_cnn, which=options.training.load_which, keys=['cnn'])
        if options.training.load_mlp:
            write_flush(cfm('INFO', 'y', 'b') + ': Loading pre-trained MLP from %s ...\n' %(options.training.load_mlp))
            model.load_submodel(options.training.load_mlp, which=options.training.load_which, keys=['mlp'])


    # ===========================================================================================================================
    #   Initialise datasets. 
    train_dataset                               = PatientDataset(options, split_name='train')
    val_dataset                                 = PatientDataset(options, split_name='val')
    if options.training.test:
        test_dataset                            = PatientDataset(options, split_name='test')

    # ===========================================================================================================================
    #   Attributes to use. 
    attr_to_use                                 = options.model.attr_to_use


    # ===========================================================================================================================
    #   Target variables for healthy and sick patients. 
    healthy_target                              = torch.FloatTensor(1).fill_(HEALTHY_TARGET)
    sick_target                                 = torch.FloatTensor(1).fill_(SICK_TARGET)
    if options.training.cuda:
        healthy_target                          = healthy_target.cuda()
        sick_target                             = sick_target.cuda()

    if options.training.loss == 'bce':
        loss_fn                                 = F.binary_cross_entropy_with_logits
        healthy_target                          = healthy_target.float()
        sick_target                             = sick_target.float()
    elif options.training.loss == 'nll':
        loss_fn                                 = F.cross_entropy
        healthy_target                          = healthy_target.long()
        sick_target                             = sick_target.long()

    # ===========================================================================================================================
    def get_gate_target(img_score=None, attr_score=None, target=None):
        the_two_scores                          = [attr_score, img_score]

        if target == 0:
            gate_target                         = np.argmin(the_two_scores)
        elif target == 1:
            gate_target                         = np.argmax(the_two_scores)
        return gate_target
            

    # ===========================================================================================================================
    #   Create closure to run the model on a data point and compute losses. 
    def closure(data_point, test=False, ae_only=False):
        p_root                                  = data_point[PROOT][0]
        p_mask                                  = data_point[MROOT][0]
        # Create a dictionary of attributes. 
        p_attr                                  = {}
        for _attr in options.model.attr_to_use:
            p_attr[_attr]                       = data_point[_attr]

        # Get the ground truth label. 
        p_label                                 = data_point[LABEL]

        result_dict                             = model(p_root, p_attr, p_mask=p_mask, test=test, ae_only=ae_only)
    
        p_score                                 = result_dict['p_score']

        loss                                    = 0


        if 'G' in options.model.system_mode or 'M' in options.model.system_mode:
            # Dictionary to return all losses and predictions.
            return_dict                         = {}

            # Train with gating network.
            w_img_score                         = result_dict['w_img_score']
            p_img_score_agg                     = result_dict['agg_img_score']
            p_attr_score                        = result_dict['attr_score']
            p_score                             = result_dict['p_score']

#   == Trying mixture of experts model with error over average error instead of average prediction ==
#   Change to mixture of experts model. See commented line for earlier implementation. Date: 2019-09-10. 
            if 'G' in options.model.system_mode:
                if p_label.item() == 0:
                    loss_imgs                   = loss_fn(p_img_score_agg, healthy_target.expand(p_img_score_agg.size(0)))
                    loss_attrs                  = loss_fn(p_attr_score, healthy_target.expand(p_attr_score.size(0)))
    #                loss                        = 0.5 * (w_img_score[:,0] * loss_imgs + (1 - w_img_score[:,0]) * loss_attrs)
                    loss                        = -1 * options.training.scale_0 * torch.log(1 - p_score)        # Negative log likelihood. 
                else:
                    loss_imgs                   = loss_fn(p_img_score_agg, sick_target)
                    loss_attrs                  = loss_fn(p_attr_score, sick_target)
    #                loss                        = 0.5 * (w_img_score[:,0] * loss_imgs + (1 - w_img_score[:,0]) * loss_attrs)
                    loss                        = -1 * options.training.scale_1 * torch.log(p_score)            # Negative log likelihood. 
            elif 'M' in options.model.system_mode:
                if p_label.item() == 0:
                    loss_imgs                   = loss_fn(p_img_score_agg, healthy_target.expand(p_img_score_agg.size(0)))
                    loss_attrs                  = loss_fn(p_attr_score, healthy_target.expand(p_attr_score.size(0)))
                    pred_prob                   = NORM_FACTOR * (
                                                    w_img_score[:,0] * torch.exp(-0.5 * (p_img_score_agg - NOISY_NEG_TARGET) ** 2) + 
                                                    (1 - w_img_score[:,0]) * torch.exp(-0.5 * (p_attr_score - NOISY_NEG_TARGET) ** 2)
                                                  )
                    loss                        = -1 * options.training.scale_0 * torch.log(pred_prob)
                else:
                    loss_imgs                   = loss_fn(p_img_score_agg, sick_target)
                    loss_attrs                  = loss_fn(p_attr_score, sick_target)
                    pred_prob                   = NORM_FACTOR * (
                                                    w_img_score[:,0] * torch.exp(-0.5 * (p_img_score_agg - NOISY_POS_TARGET) ** 2) + 
                                                    (1 - w_img_score[:,0]) * torch.exp(-0.5 * (p_attr_score - NOISY_POS_TARGET) ** 2)
                                                  )
                    loss                        = -1 * options.training.scale_1 * torch.log(pred_prob)
#   =====================
#   =====================
#            if p_label.item() == 0: 
#                loss                            = loss_fn(
#                                                    p_score,
#                                                    healthy_target.expand(p_score.size(0)),
#                                                  )
#            else:
#                loss                            = loss_fn(
#                                                    p_score,
#                                                    sick_target,
#                                                  )
#   =====================

            if 'T' in options.model.system_mode:
            # Train only the gating network, with targets defined by the scores of the classifiers 
            #    on images and attributes. 
                gate_target                         = get_gate_target(img_score=p_img_score_agg.item(), 
                                                                      attr_score=p_attr_score.item(), 
                                                                      target=p_label.item())
                if gate_target == 0:
                    gate_target                     = healthy_target
                else:
                    gate_target                     = sick_target

                loss                               += F.binary_cross_entropy(w_img_score.squeeze(1), gate_target)

                return_dict['gate_target']          = gate_target



            return_dict['total']                = loss

            return_dict['imgs']                 = loss_imgs

            return_dict['attrs']                = loss_attrs

            return_dict['recon']                = None
            return_dict['sparse']               = None
            return_dict['img_inputs']           = None
            return_dict['img_recons']           = None

            return_dict['w_img_score']          = w_img_score.item()
            return_dict['p_score']              = p_score.item()
            return_dict['pred']                 = p_score.item()

            return_dict['p_img_score_agg']      = result_dict['agg_img_score']
            return_dict['p_attr_score']         = result_dict['attr_score']

            return return_dict

        # Train on images. 
        if 'I' in options.model.system_mode:
            p_img_scores                        = result_dict['img_scores']
            p_img_latent_enc                    = result_dict['p_img_latent_enc']
            p_img_score_agg                     = result_dict['agg_img_score']

            # Healthy patient loss: All images get label 0. 
            if p_label.item() == 0:
                if not test:
                    # In train mode, healthy loss is computed on all images, 
                    loss_imgs                   = loss_fn(
                                                    p_img_score_agg, 
                                                    healthy_target.expand(p_img_score_agg.size(0)),
                                                  )
                else:
                    # In test mode, it is computed on the aggregated score only. 
                    loss_imgs                   = loss_fn(
                                                    p_img_score_agg,
                                                    healthy_target,
                                                  )
                # Scale according to healthy/sick
                loss_imgs                       = options.training.scale_0 * loss_imgs

            # Sick patient loss: Only the aggregated value gets label 1. 
            elif p_label.item() == 1:
                loss_imgs                       = loss_fn(
                                                    p_img_score_agg,
                                                    sick_target,
                                                  )
                # Scale according to healthy/sick
                loss_imgs                       = options.training.scale_1 * loss_imgs

            # Scale loss_imgs
            loss_imgs                           = options.training.scale_imgs * loss_imgs
            # Add to the total loss only if we are training with image labels as well
            if not ae_only:
                loss                            = loss + loss_imgs
        else:
            # No loss from images. 
            loss_imgs                           = None

        # Train on attributes as well. 
        if 'A' in options.model.system_mode:
            p_attr_score                        = result_dict['attr_score']

            if p_label.item() == 0:
                loss_attrs                      = loss_fn(
                                                    p_attr_score, 
                                                    healthy_target,
                                                  )
                # Scale according to healthy/sick
                loss_attrs                      = options.training.scale_0 * loss_attrs
            else:
                loss_attrs                      = loss_fn(
                                                    p_attr_score,
                                                    sick_target,
                                                  )
                # Scale according to healthy/sick
                loss_attrs                      = options.training.scale_1 * loss_attrs
            # Scale loss_attrs
            loss_attrs                          = options.training.scale_attrs * loss_attrs
            # Add to the total loss only if we are training with image labels as well. 
            if not ae_only:
                loss                            = loss + loss_attrs
        else:
            loss_attrs                          = None

        # Train encoder-decoder as well. 
        if 'D' in options.model.system_mode:
            loss_recon                          = result_dict['loss_recon']
            loss_sparse                         = result_dict['loss_sparse']
            img_recons                          = result_dict['img_recons']
            img_inputs                          = result_dict['img_inputs']
            # Scale loss recon
            loss_recon                          = options.training.scale_recon * loss_recon
            loss_sparse                         = options.training.scale_sparse * loss_sparse
            # Add to the total loss
            loss                                = loss + loss_recon + loss_sparse
        else:
            loss_recon                          = None
            loss_sparse                         = None
            img_recons                          = None
            img_inputs                          = None

        # Dictionary to return all losses and predictions.
        return_dict                             = {}
        return_dict['total']                    = loss

        return_dict['imgs']                     = loss_imgs

        return_dict['attrs']                    = loss_attrs

        return_dict['recon']                    = loss_recon
        return_dict['sparse']                   = loss_sparse
        return_dict['img_inputs']               = img_inputs
        return_dict['img_recons']               = img_recons

        if options.training.loss == 'bce':
            return_dict['p_score']              = p_score.item()
            return_dict['pred']                 = F.sigmoid(p_score).item()
        elif options.training.loss == 'nll':
            return_dict['p_score']              = p_score[:,1].item()
            return_dict['pred']                 = F.softmax(p_score, dim=1)[:,1].item()

        return return_dict

        
            
    while not options.training.skip_train and iter_mark < options.training.n_iters:
        # =======================================================================================================================
        #   Training loop. 
        print(cfm('INFO', 'y', 'b') + ': Training')

        #   Put the model in train mode.
        model.train()
        #   Create dataloader. 
        train_dataloader                        = torch.utils.data.DataLoader(
                                                    train_dataset,
                                                    shuffle=True,
                                                    batch_size=1,
                                                    num_workers=1
                                                  )

        total_train_loss                        = 0
        total_train_imgs_loss                   = 0
        total_train_attrs_loss                  = 0
        total_train_recon_loss                  = 0
        total_train_sparse_loss                 = 0

        total_val_loss                          = 0
        total_val_imgs_loss                     = 0
        total_val_attrs_loss                    = 0
        total_val_recon_loss                    = 0
        total_val_sparse_loss                   = 0

        accumulated_examples                    = 0
        for batch_idx, data_point in enumerate(train_dataloader):
            # We can pass only one patient through the model at once, but we will 
            #   keep accumulating gradients until we pass subj_batch_size patients. 
            if accumulated_examples == 0:
                model.reset_gradients()

            # Do not train with sick patients initially. 
            if data_point[LABEL].item() == 1 and tr_epoch < options.training.neg_train_until:
                continue

            # Evaluate closure with test=False because this is the training phase. 
            ae_only                             = not (tr_epoch >= options.training.autoencoder_only_epochs)
            loss_pred_dict                      = closure(data_point, test=False, ae_only=ae_only)

            loss_total                          = loss_pred_dict['total']
            loss_imgs                           = loss_pred_dict['imgs']
            loss_attrs                          = loss_pred_dict['attrs']
            loss_recon                          = loss_pred_dict['recon']
            loss_sparse                         = loss_pred_dict['sparse']
            pred                                = loss_pred_dict['pred'] 
            p_score                             = loss_pred_dict['p_score']
            if 'G' in options.model.system_mode or 'M' in options.model.system_mode:
                p_img_score                     = loss_pred_dict['p_img_score_agg'].item()
                p_attr_score                    = loss_pred_dict['p_attr_score'].item()
                w_img_score                     = loss_pred_dict['w_img_score']

            #   Learning if this is a training loop.
            if not ae_only:
                # Compute gradients, but only if this is a training loop. 
                loss_total.backward()

            accumulated_examples               += 1
            if accumulated_examples == options.training.subj_batch_size or batch_idx + 1 == len(train_dataloader):
                # Take the average gradient over the examples seen so far. 
                for __mname in model.models_dict:
                    for param in model.models_dict[__mname].parameters():
                        if param.grad is not None:
                            param.grad         *= 1./accumulated_examples
    
                accumulated_examples            = 0
                model.take_optimiser_step()

            write_flush(cfm('STEP', 'b', 'b') + ': Iteration %d. Epoch %d, batch %d . LR = %g | GT = %d . p_score = %.4f .  loss_total = %.4f' 
                    %(iter_mark, tr_epoch, batch_idx, current_lr, data_point[LABEL].item(), p_score, loss_total.item()))
            if loss_imgs is not None:
                write_flush(' . loss_imgs: %.4f' %(loss_imgs.item()))
                total_train_imgs_loss          += loss_imgs.item()
            if loss_attrs is not None:
                write_flush(' . loss_attrs: %.4f' %(loss_attrs.item()))
                total_train_attrs_loss         += loss_attrs.item()
            if loss_recon is not None:
                write_flush(' . loss_recon: %.4f' %(loss_recon.item()))
                total_train_recon_loss         += loss_recon.item()
            if loss_sparse is not None:
                write_flush(' . loss_sparse: %.4f' %(loss_sparse.item()))
                total_train_sparse_loss        += loss_sparse.item()
            if 'G' in options.model.system_mode or 'M' in options.model.system_mode:
                write_flush(' . Pred:  [images: %.4f, attributes: %.4f] Gate:  [images: %.4f, attributes: %.4f]' %(p_img_score, p_attr_score, w_img_score, 1 - w_img_score))
            write_flush('\n')

            total_train_loss                   += loss_total.item()

            if options.training.do_logging:
                writer.add_scalar('iter_train_loss', loss_total.item(), tr_epoch)

            # Increment iter_mark
            iter_mark                          += 1
            #  If iter_mark is at lr_decay_steps, reduce learning rate. 
            if options.training.lr_decay_scheme == 'step' and iter_mark in options.training.lr_decay_steps:
                current_lr                     *= options.training.lr_decay
                model.reduce_lr()
    
        #   Take average loss over dataset.
        total_train_loss                        = total_train_loss / len(train_dataset)
        total_train_imgs_loss                   = total_train_imgs_loss / len(train_dataset)
        total_train_attrs_loss                  = total_train_attrs_loss / len(train_dataset)
        total_train_recon_loss                  = total_train_recon_loss / len(train_dataset)
        total_train_sparse_loss                 = total_train_sparse_loss / len(train_dataset)

        #   If loss does not change significantly for fifty iterations. 
        if options.training.lr_decay_scheme == 'plateau' and tr_epoch > 0:
            if len(train_val_losses) < _epochs_lr_criterion:
                if np.abs(np.mean(np.array(train_val_losses)[:,0]) - total_train_loss) < _reduce_lr_criterion:
                    model.reduce_lr()
                    current_lr                 *= options.training.lr_decay
                    _reduce_lr_criterion        = _reduce_lr_criterion * 0.1
            elif (np.abs(np.mean(np.array(train_val_losses)[-_epochs_lr_criterion:,0]) - total_train_loss) < _reduce_lr_criterion):
                model.reduce_lr()
                current_lr                     *= options.training.lr_decay
                _reduce_lr_criterion            = _reduce_lr_criterion * 0.1

        # =======================================================================================================================
        #   Validation loop. 
        print(cfm('INFO', 'y', 'b') + ': Validating')

        #   Put the model in eval mode
        model.eval()
        #   Create dataloader. 
        val_dataloader                          = torch.utils.data.DataLoader(
                                                    val_dataset,
                                                    shuffle=False,
                                                    batch_size=1, 
                                                    num_workers=1
                                                  )

        # Create a dictionary to store predictions on the val set for each epoch. 
        pred_this_epoch                         = {}

        for batch_idx, data_point in enumerate(val_dataloader):
            p_root                              = data_point[PROOT][0]
            p_label                             = data_point[LABEL]
            p_id                                = os.path.split(p_root)[-1]

            # Evaluate the closure with test=True because this is the validation phase. 
            if tr_epoch >= options.training.autoencoder_only_epochs: 
                loss_pred_dict                  = closure(data_point, test=True, ae_only=False)
            else:
                loss_pred_dict                  = closure(data_point, test=True, ae_only=True)

            loss_total                          = loss_pred_dict['total']
            loss_imgs                           = loss_pred_dict['imgs']
            loss_attrs                          = loss_pred_dict['attrs']
            loss_recon                          = loss_pred_dict['recon']
            loss_sparse                         = loss_pred_dict['sparse']
            p_score                             = loss_pred_dict['p_score']
            pred                                = loss_pred_dict['pred'] 
            if 'G' in options.model.system_mode or 'M' in options.model.system_mode:
                p_img_score                     = loss_pred_dict['p_img_score_agg'].item()
                p_attr_score                    = loss_pred_dict['p_attr_score'].item()
                w_img_score                     = loss_pred_dict['w_img_score']

            write_flush(cfm('VAL ', 'b', 'b') + ': Patient ID: %15s | GT = %d . Prediction = %.4f . p_score = %.4f | loss_total = %.4f' 
                    %(p_id, p_label.item(), pred, p_score, loss_total.item()))
            if loss_imgs is not None:
                write_flush(' . loss_imgs: %.4f' %(loss_imgs.item()))
                total_val_imgs_loss            += loss_imgs.item()
            if loss_attrs is not None:
                write_flush(' . loss_attrs: %.4f' %(loss_attrs.item()))
                total_val_attrs_loss           += loss_attrs.item()
            if loss_recon is not None:
                write_flush(' . loss_recon: %.4f' %(loss_recon.item()))
                total_val_recon_loss           += loss_recon.item()
            if loss_sparse is not None:
                write_flush(' . loss_sparse: %.4f' %(loss_sparse.item()))
                total_val_sparse_loss          += loss_sparse.item()
            if 'G' in options.model.system_mode or 'M' in options.model.system_mode:
                write_flush(' . Pred:  [images: %.4f, attributes: %.4f] Gate:  [images: %.4f, attributes: %.4f]' %(p_img_score, p_attr_score, w_img_score, 1 - w_img_score))
            write_flush('\n')

            #   Add to the predictions dictionary. 
            pred_this_epoch[p_id]               = [p_label.item(), pred]

            total_val_loss                     += loss_total.item()

        #   Take average loss over dataset.
        total_val_loss                          = total_val_loss / len(val_dataset)
        total_val_imgs_loss                     = total_val_imgs_loss / len(val_dataset)
        total_val_attrs_loss                    = total_val_attrs_loss / len(val_dataset)
        total_val_recon_loss                    = total_val_recon_loss / len(val_dataset)
        total_val_sparse_loss                   = total_val_sparse_loss / len(val_dataset)

        write_flush('\n' + cfm('INFO', 'y', 'b') + ': Epoch %d summary.\n' %(tr_epoch))
        loss_names                              = ['Train loss', 'Val loss']
        loss_values                             = [total_train_loss, total_val_loss]
        if 'I' in options.model.system_mode:
            loss_names                         += ['Train Imgs loss', 'Val Imgs loss']
            loss_values                        += [total_train_imgs_loss, total_val_imgs_loss]
        if 'A' in options.model.system_mode:
            loss_names                         += ['Train Attrs loss', 'Val Attrs loss']
            loss_values                        += [total_train_attrs_loss, total_val_attrs_loss]
        if 'D' in options.model.system_mode:
            loss_names                         += ['Train Recon loss', 'Val Recon loss', 'Train Sparse loss', 'Val Sparse loss']
            loss_values                        += [total_train_recon_loss, total_val_recon_loss, total_train_sparse_loss, total_val_sparse_loss]

        for _loss_name, _loss_value in zip(loss_names, loss_values):
            write_flush('\t%20s: %.4f\n' %(_loss_name, _loss_value))

        # Save predictions on val set. 
        val_pred_file                           = os.path.join(options.output_dir, 'val_pred', '%06d.npy' %(tr_epoch))
        with open(val_pred_file, 'wb') as fp:
            pickle.dump(pred_this_epoch, fp)
        print('Saved val predictions to %s.' %(val_pred_file))

        print(' === ')

        #   Increment epoch. 
        tr_epoch                               += 1
  
        # = Logging =

        #   Dump system state. 
        train_val_losses.append([total_train_loss, total_val_loss])
      
        #   Dump tensorboard logs. 
        if options.training.do_logging:
            writer.add_scalar('train_loss', total_train_loss, tr_epoch)
            writer.add_scalar('val_loss', total_val_loss, tr_epoch)

            if 'I' in options.model.system_mode:
                writer.add_scalar('train_imgs_loss', total_train_imgs_loss, tr_epoch)
                writer.add_scalar('val_imgs_loss', total_val_imgs_loss, tr_epoch)

            if 'A' in options.model.system_mode:
                writer.add_scalar('train_attrs_loss', total_train_attrs_loss, tr_epoch)
                writer.add_scalar('val_attrs_loss', total_val_attrs_loss, tr_epoch)

            if 'D' in options.model.system_mode:
                writer.add_scalar('train_recon_loss', total_train_recon_loss, tr_epoch)
                writer.add_scalar('val_recon_loss', total_val_recon_loss, tr_epoch)
                writer.add_scalar('train_sparse_loss', total_train_sparse_loss, tr_epoch)
                writer.add_scalar('val_sparse_loss', total_val_sparse_loss, tr_epoch)

                # Save images. 
                img_inputs                      = loss_pred_dict['img_inputs']
                img_recons                      = loss_pred_dict['img_recons']

                inputs_grid                     = vutils.make_grid(img_inputs[:9,:,:,:], nrow=3, padding=2, normalize=True)
                recons_grid                     = vutils.make_grid(img_recons[:9,:,:,:], nrow=3, padding=2, normalize=True)

                writer.add_image('inputs_grid', inputs_grid, tr_epoch)
                writer.add_image('recons_grid', recons_grid, tr_epoch)

        if tr_epoch == 1 or (tr_epoch > 1 and total_val_loss < np.min(np.array(train_val_losses)[:-1, 1])):
            model.checkpoint(best=True)
            system_state                        = {
                'tr_epoch'                      : tr_epoch,
                'iter_mark'                     : iter_mark,
                'train_val_losses'              : train_val_losses,
            }
            with open(os.path.join(options.output_dir, 'best_model/', 'system_state.pkl'), 'wb') as fp:
                pickle.dump(system_state, fp)
 

        #   Dump models. 
        if tr_epoch % options.training.checkpoint_every == 0:
            model.checkpoint()
            system_state                        = {
                'tr_epoch'                      : tr_epoch,
                'iter_mark'                     : iter_mark,
                'train_val_losses'              : train_val_losses,
            }
            with open(os.path.join(options.output_dir, 'system_state.pkl'), 'wb') as fp:
                pickle.dump(system_state, fp)

        # End of train loop. 
        # =======================================================================================================================


    if options.training.test:
        # =======================================================================================================================
        #   Testing loop. 
#        print(cfm('INFO', 'y', 'b') + ': Testing with the best model according to the validation loss.')
#        model.load_checkpoint(options.output_dir, which='best')

        #   Put the model in eval mode
        model.eval()
        #   Create dataloader. 
        test_dataloader                         = torch.utils.data.DataLoader(
                                                    test_dataset,
                                                    shuffle=False, 
                                                    batch_size=1, 
                                                    num_workers=1
                                                  )
        # Initialise losses. 
        total_test_loss                         = 0
        total_test_imgs_loss                    = 0
        total_test_attrs_loss                   = 0
        total_test_recon_loss                   = 0
        total_test_sparse_loss                  = 0

        # Create a dictionary to store predictions on the val set for each epoch. 
        pred_this_epoch                         = {}

        for batch_idx, data_point in enumerate(test_dataloader):
            p_root                              = data_point[PROOT][0]
            p_label                             = data_point[LABEL]
            p_id                                = os.path.split(p_root)[-1]

            # Evaluate the closure with test=True because this is the validation phase. 
            loss_pred_dict                      = closure(data_point, test=True)

            loss_total                          = loss_pred_dict['total']
            loss_imgs                           = loss_pred_dict['imgs']
            loss_attrs                          = loss_pred_dict['attrs']
            loss_recon                          = loss_pred_dict['recon']
            loss_sparse                         = loss_pred_dict['sparse']
            p_score                             = loss_pred_dict['p_score']
            pred                                = loss_pred_dict['pred'] 
            if 'G' in options.model.system_mode or 'M' in options.model.system_mode:
                p_img_score                     = loss_pred_dict['p_img_score_agg'].item()
                p_attr_score                    = loss_pred_dict['p_attr_score'].item()
                w_img_score                     = loss_pred_dict['w_img_score']

            write_flush(cfm('TEST', 'b', 'b') + ': Patient ID: %15s | GT = %d . Prediction = %.4f . p_score = %.4f | loss_total = %.4f' 
                    %(p_id, p_label.item(), pred, p_score, loss_total.item()))
            if loss_imgs is not None:
                write_flush(' . loss_imgs: %.4f' %(loss_imgs.item()))
                total_test_imgs_loss           += loss_imgs.item()
            if loss_attrs is not None:
                write_flush(' . loss_attrs: %.4f' %(loss_attrs.item()))
                total_test_attrs_loss          += loss_attrs.item()
            if loss_recon is not None:
                write_flush(' . loss_recon: %.4f' %(loss_recon.item()))
                total_test_recon_loss          += loss_recon.item()
            if loss_sparse is not None:
                write_flush(' . loss_sparse: %.4f' %(loss_sparse.item()))
                total_test_sparse_loss          += loss_sparse.item()
            if 'G' in options.model.system_mode or 'M' in options.model.system_mode:
                write_flush(' . Pred:  [images: %.4f, attributes: %.4f] Gate:  [images: %.4f, attributes: %.4f]' %(p_img_score, p_attr_score, w_img_score, 1 - w_img_score))
            write_flush('\n')

            #   Add to the predictions dictionary. 
            pred_this_epoch[p_id]               = [p_label.item(), pred]

            total_test_loss                    += loss_total.item()

        # Divide by dataset size --- average over the dataset. 
        total_test_loss                         = total_test_loss / len(test_dataset)
        total_test_imgs_loss                    = total_test_imgs_loss / len(test_dataset)
        total_test_attrs_loss                   = total_test_attrs_loss / len(test_dataset)
        total_test_recon_loss                   = total_test_recon_loss / len(test_dataset)
        total_test_sparse_loss                  = total_test_sparse_loss / len(test_dataset)

        write_flush(cfm('INFO', 'y', 'b') + ': Testset loss summary: \n')
        loss_names                              = ['Test loss']
        loss_values                             = [total_test_loss]
        if 'I' in options.model.system_mode:
            loss_names                         += ['Test Imgs loss']
            loss_values                        += [total_test_imgs_loss]
        if 'A' in options.model.system_mode:
            loss_names                         += ['Test Attrs loss']
            loss_values                        += [total_test_attrs_loss]
        if 'D' in options.model.system_mode:
            loss_names                         += ['Test Recon loss', 'Test Sparse loss']
            loss_values                        += [total_test_recon_loss, total_test_sparse_loss]

        for _loss_name, _loss_value in zip(loss_names, loss_values):
            write_flush('\t%16s: %.4f\n' %(_loss_name, _loss_value))

        # Save predictions on val set. 
        test_pred_file                          = os.path.join(options.output_dir, 'test_pred', 'best.npy')
        with open(test_pred_file, 'wb') as fp:
            pickle.dump(pred_this_epoch, fp)
        print('Saved test predictions to %s.' %(test_pred_file))
        print(' === ')


# ===============================================================================================================================



#   Call main
if __name__ == '__main__':
    main()

