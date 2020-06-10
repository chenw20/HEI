import numpy as np
import tensorflow as tf
import json
import os
import time

from tensorflow.examples.tutorials.mnist import input_data

from core.hamInfnet_hei import HamInfNetHEI as HamInfNet
from decoder.vae_dcnn_mnist import VAE_DCNN_GPU, VAEQ_CONV
from util.constant import log_2pi
from util.utils import dybinarize_mnist


def training_setting(z_dim):
    setting = {'mb_size': 128,
               'alpha': 0.3,
               'z_dim': z_dim,
               'h_dim': 500,  #500
               'X_mnist_dim': 28 ** 2,
               'momentum_train_batch_size': 1,
               'z_train_sample_batch_size': 20,  #1
               'num_layers': 30,   #30
               'num_lfsteps': 5,
               'momentum_std': 1.0,
               'generator': 'dcnn_relu',
               'batches': 50000,   #500000
               'dybin': True,
               'reg': 0.000001,
               'lr': 0.0002, #0.0002
               'lr-decay': 0.97,
               }
    return setting


def train(setting, dataset, dataset_name='mnist', save_model=False, device='CPU', dtype=tf.float32):
    mb_size = setting['mb_size']  # 640
    alpha = setting['alpha']
    z_dim = setting['z_dim']  # 50
    h_dim = setting['h_dim']  # 100
    X_mnist_dim = setting['X_mnist_dim']  # 28**2
    momentum_train_batch_size = setting['momentum_train_batch_size']  # 1
    z_train_sample_batch_size = setting['z_train_sample_batch_size']  # 1
    generator = setting['generator']
    num_layers = setting['num_layers']  # 5
    num_lfsteps = setting['num_lfsteps']  # 5
    momentum_std = setting['momentum_std']  # 1.0
    batches = setting['batches']  # 1000
    dybin = setting['dybin']  # True
    reg = setting['reg']
    lr = setting['lr']
    lr_decay = setting['lr-decay']
    if 'vfun' in setting.keys():
        vfun = setting['vfun']
    else:
        vfun = 'sigmoid'

    bin_label = 'dybin'
    if not dybin:
        bin_thresh = setting['bin_thresh']
        bin_label = 'stbin'

    setting = {'mb_size': mb_size,
               'alpha': alpha,
               'z_dim': z_dim,
               'h_dim': h_dim,
               'X_mnist_dim': X_mnist_dim,
               'momentum_train_batch_size': momentum_train_batch_size,
               'z_train_sample_batch_size': z_train_sample_batch_size,
               'num_layers': num_layers,
               'num_lfsteps': num_lfsteps,
               'momentum_std': momentum_std,
               'generator': generator,
               'vfun': vfun,
               'batches': batches,
               'dybin': dybin,
               'reg': reg,
               'lr': lr,
               'lr-decay': lr_decay,
               }
    if not dybin:
        setting['bin_thresh'] = bin_thresh
    model_name = "vae_{}-hei-{}-alpha{:.0e}-zd{}-hd{}-mbs{}-mbn{}-h{}-l{}-reg{:.0e}-{}-lr{:.0e}".format(generator,
                                                                                              dataset_name,
                                                                                              alpha,
                                                                                              z_dim,
                                                                                              h_dim, mb_size,
                                                                                              batches,
                                                                                              num_layers,
                                                                                              num_lfsteps,
                                                                                              reg, bin_label, lr)
    output_dir = "model/debug2/"

    #output_dir = "model/alphadiv_encdec_rbf_30ly_20sam/"

    if save_model:
        os.mkdir(output_dir + '{}'.format(model_name))
        #ckpt_name = output_dir + '{}/{}.ckpt'.format(model_name, model_name)
        ckpt_name = output_dir + '{}.ckpt'.format(model_name)
        setting_filename = output_dir + '{}/setting.json'.format(model_name)
        with open(setting_filename, 'w') as f:
            json.dump(setting, f)
        
            

    global_step = tf.Variable(0, trainable=False, name='global_step')
    global_step2 = tf.Variable(0, trainable=False, name='global_step2')

    device_config = '/device:{}:0'.format(device)
    with tf.device(device_config):
        X_batch_train = tf.placeholder(dtype, shape=[mb_size, X_mnist_dim])
        vaeq = VAEQ_CONV(alpha = alpha, h_dim=h_dim, z_dim=z_dim)
        vae = VAE_DCNN_GPU(h_dim=h_dim, z_dim=z_dim)

        def gen_fun_train_hmc(sample_batch_size, input_data_batch_size ,inflation):
            mu, log_var = vaeq.Q(X_batch_train)
            mu_nograd = tf.stop_gradient(mu)  
            log_var_nograd = tf.stop_gradient(log_var)   
            inflation = tf.stop_gradient(inflation)
            #inflation = tf.exp(log_inflation)   # avoid ksd overinflates 
            eps = tf.random_normal(shape=(sample_batch_size, input_data_batch_size, z_dim))
            logp = -0.5 * tf.reduce_sum((inflation*eps) ** 2 + log_2pi + 2*tf.log(inflation) + log_var_nograd, axis=-1)
            return eps * (inflation* tf.exp(log_var_nograd / 2)) + mu_nograd, logp

        def gen_fun_train_ksd(sample_batch_size, input_data_batch_size ,inflation):
            mu, log_var = vaeq.Q(X_batch_train)
            #inflation = tf.exp(log_inflation)
            mu_nograd = tf.stop_gradient(mu)
            log_var_nograd = tf.stop_gradient(log_var)
            eps = tf.random_normal(shape=(sample_batch_size, input_data_batch_size, z_dim))
            logp = -0.5 * tf.reduce_sum((inflation*eps) ** 2 + log_2pi + 2*tf.log(inflation) + log_var_nograd, axis=-1)
            return eps * (inflation* tf.exp(log_var_nograd / 2)) + mu_nograd, logp

        pot_fun_train = lambda state: vae.pot_fun_train(data_x=X_batch_train, sample_z=state)
        pot_fun_not_train = lambda state: vae.pot_fun_not_train(data_x=X_batch_train, sample_z=state)

        hamInfNet_hm = HamInfNet(num_layers=num_layers, num_lfsteps=num_lfsteps,
                                 sample_dim=z_dim, dtype=dtype)
        #neg_pot, recon_mean, elbo_x_mean = hamInfNet_hm.build_elbo_graph(
        neg_pot, recon_mean = hamInfNet_hm.build_elbo_graph(
            pot_fun=pot_fun_train,
            state_init_gen=gen_fun_train_hmc,
            input_data_batch_size=mb_size,
            #sample_batch_size=z_train_sample_batch_size,            
            sample_batch_size=1,
            training=True
        )
        pot_batch_mean = -neg_pot    # pot_batch_mean is neg-log-lik
       
        ksd_mean = hamInfNet_hm.build_ksd_graph(
            pot_fun=pot_fun_not_train,
            state_init_gen=gen_fun_train_ksd,
            input_data_batch_size=mb_size,
            sample_batch_size=z_train_sample_batch_size,
            training=True
        )
        
        
        q_loss1 = vaeq.create_loss_train(vae, X_batch_train, batch_size = 1, loss_only=True)
        
        
        q_loss2 = vaeq.create_loss_not_train(vae, X_batch_train, batch_size = 1, loss_only=True)
        """
        #loss =  pot_batch_mean + q_loss  + reg * (vae.get_parameters_reg() + vaeq.get_parameters_reg())
        loss =  pot_batch_mean + q_loss +ksd_mean + reg * (vae.get_parameters_reg() + vaeq.get_parameters_reg())

        starter_learning_rate = lr
        learning_rate = tf.train.exponential_decay(starter_learning_rate, global_step,
                                                   1000, lr_decay, staircase=True)
        optimizer = tf.train.AdamOptimizer(learning_rate=learning_rate)
        train_op = optimizer.minimize(loss, global_step=global_step)
        """
        infl = hamInfNet_hm.getInflation() 
        
        loss = q_loss2 + ksd_mean + pot_batch_mean + reg * (vae.get_parameters_reg() + vaeq.get_parameters_reg())

        starter_learning_rate = lr
        learning_rate = tf.train.exponential_decay(starter_learning_rate, global_step,
                     1000, lr_decay, staircase=True)
        optimizer = tf.train.AdamOptimizer(learning_rate=learning_rate)
        train_op = optimizer.minimize(q_loss1, global_step=global_step)
        
        optimizer2 = tf.train.AdamOptimizer(learning_rate=learning_rate)
        train_op2 = optimizer2.minimize(loss, global_step=global_step2)
        """
        q_loss = vaeq.create_loss(vae, X_batch_train, batch_size = z_train_sample_batch_size, loss_only=True)
        optimizer2 = tf.train.AdamOptimizer(learning_rate=learning_rate)
        train_op2 = optimizer2.minimize(q_loss, global_step=global_step2)
        """
        
        
    loss_seq = []
    loss_q_seq = []
    pot_seq = []
    recon_seq = []
    ksd_seq = []
    saved_variables = vae.get_parameters() + hamInfNet_hm.getParams() + vaeq.get_parameters()
    saver = tf.train.Saver(saved_variables, max_to_keep=10)
    print(saved_variables)
    
    log = []

    with tf.Session() as sess:
        sess.run(tf.global_variables_initializer())
        checkpoint_batch = 5000 #1000
        total_time = 0
        for i in np.arange(0, 100000):
            X_mb_raw, _ = dataset.train.next_batch(mb_size)
            X_mb = dybinarize_mnist(X_mb_raw)

            start = time.time()
            
            _, q_loss_i, inflation_i = sess.run(
                [train_op, q_loss1, infl],
                feed_dict={X_batch_train: X_mb})
            """
            _, loss_i, q_loss_i,  pot_mean_i, recon_mean_i, inflation_i = sess.run(
                [train_op, loss, q_loss, pot_batch_mean, recon_mean, inflation],
                feed_dict={X_batch_train: X_mb})
            """
            #pot_seq.append(pot_mean_i)
            end = time.time()
            total_time += end - start
            #loss_seq.append(loss_i)
            loss_q_seq.append(q_loss_i)
            #ksd_seq.append(ksd_mean_i)
            #recon_seq.append(recon_mean_i)
            if i % 1000 ==999:
            #if True:
                """
                log_line = 'iter: {}, loss: {}, q_loss: {}, pot: {}, recon: {}, inflation:{}, time: {}'.format(i + 1,
                                                                                     np.mean(np.array(loss_seq)),
                                                                                     np.mean(np.array(loss_q_seq)),
                                                                                     np.mean(np.array(pot_seq)),
                                                                                     np.mean(np.array(recon_seq)),
                                                                                     inflation_i,total_time)
                
                """                                                                    
                log_line = 'iter: {}, q_loss: {},  inflation:{}, time: {}'.format(i + 1,
                                                                                     #np.mean(np.array(loss_seq)),
                                                                                     np.mean(np.array(loss_q_seq)),
                                                                                     #np.mean(np.array(ksd_seq)),
                                                                                     #np.mean(np.array(pot_seq)),
                                                                                     #np.mean(np.array(recon_seq)),
                                                                                     inflation_i,
                                                                                     total_time)
                
                print(log_line)
                log.append(log_line + '\n')
                #loss_seq.clear()
                #pot_seq.clear()
                loss_q_seq.clear()
                #ksd_seq.clear()
                #recon_seq.clear()
                total_time = 0
            if  i % checkpoint_batch == 4999:
                    #print("model saved at iter: {}".format(i + 1))
                    #saver.save(sess, ckpt_name, global_step=global_step2)
                    with open(output_dir + '{}/training.cklog'.format(model_name), "a+") as log_file:
                        log_file.writelines(log)
                        log.clear()
            
        for i in np.arange(0, batches):
            X_mb_raw, _ = dataset.train.next_batch(mb_size)
            X_mb = dybinarize_mnist(X_mb_raw)

            start = time.time()
            
            _, loss_i, q_loss_i, ksd_mean_i, pot_mean_i, recon_mean_i,inflation_i = sess.run(
                [train_op2, loss, q_loss2, ksd_mean, pot_batch_mean, recon_mean, infl],
                feed_dict={X_batch_train: X_mb})
            """
            _, loss_i, q_loss_i,  pot_mean_i, recon_mean_i, inflation_i = sess.run(
                [train_op, loss, q_loss, pot_batch_mean, recon_mean, inflation],
                feed_dict={X_batch_train: X_mb})
            """
            pot_seq.append(pot_mean_i)
            end = time.time()
            total_time += end - start
            loss_seq.append(loss_i)
            loss_q_seq.append(q_loss_i)
            ksd_seq.append(ksd_mean_i)
            recon_seq.append(recon_mean_i)
            if i % 10 ==9:
            #if True:
                """
                log_line = 'iter: {}, loss: {}, q_loss: {}, pot: {}, recon: {}, inflation:{}, time: {}'.format(i + 1,
                                                                                     np.mean(np.array(loss_seq)),
                                                                                     np.mean(np.array(loss_q_seq)),
                                                                                     np.mean(np.array(pot_seq)),
                                                                                     np.mean(np.array(recon_seq)),
                                                                                     inflation_i,total_time)
                
                """                                                                    
                log_line = 'iter: {}, loss: {}, q_loss: {}, ksd_loss: {}, pot: {}, recon: {}, inflation:{}, time: {}'.format(i + 1,
                                                                                     np.mean(np.array(loss_seq)),
                                                                                     np.mean(np.array(loss_q_seq)),
                                                                                     np.mean(np.array(ksd_seq)),
                                                                                     np.mean(np.array(pot_seq)),
                                                                                     np.mean(np.array(recon_seq)),
                                                                                     inflation_i,
                                                                                     total_time)
                
                print(log_line)
                log.append(log_line + '\n')
                loss_seq.clear()
                pot_seq.clear()
                loss_q_seq.clear()
                ksd_seq.clear()
                recon_seq.clear()
                total_time = 0
            if save_model and i % checkpoint_batch == 4999:
                print("model saved at iter: {}".format(i + 1))
                saver.save(sess, ckpt_name, global_step=global_step2)
                with open(output_dir + '{}/training.cklog'.format(model_name), "a+") as log_file:
                    log_file.writelines(log)
                    log.clear()
        if save_model:
            saver.save(sess, ckpt_name, global_step=global_step2)
            with open(output_dir + '{}/training.cklog'.format(model_name), "a+") as log_file:
                log_file.writelines(log)
                log.clear()
    return output_dir + '{}/'.format(model_name)


if __name__ == '__main__':
    mnist = input_data.read_data_sets('data/MNIST_data', one_hot=True)
    train(setting=training_setting(32), dataset=mnist, save_model=True, device="GPU") # 32
