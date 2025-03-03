from application.utils.path_utils import get_data_path
from sklearn.utils import resample

# Taken from Huge & Savine's Differential ML github

### Initial requirements for code to work - START
import tensorflow as tf2
import numpy as np
import matplotlib.pyplot as plt

# disable eager execution etc
tf = tf2.compat.v1
#tf.disable_eager_execution()

tf.logging.set_verbosity(tf.logging.ERROR)
import warnings
warnings.filterwarnings('ignore')

# representation of real numbers in TF, change here for 32/64 bits
real_type = tf.float32

### Initial requirements for code to work - END


def integrated_relu(x):
    return tf.keras.backend.switch(x >= 0, 0.5*x**2, 0)

def vanilla_net(
        input_dim,  # dimension of inputs, e.g. 10
        hidden_units,  # units/neurons in hidden layers, assumed constant, e.g. 20
        hidden_layers,  # number of hidden layers, e.g. 4
        seed):  # seed for initialization or None for random

    # set seed

    tf.set_random_seed(seed)

    # input layer
    xs = tf.placeholder(shape=[None, input_dim], dtype=real_type)

    # connection weights and biases of hidden layers
    ws = [None]
    bs = [None]
    # layer 0 (input) has no parameters

    # layer 0 = input layer
    zs = [xs]  # eq.1, l=0

    # first hidden layer (index 1)
    # weight matrix
    ws.append(tf.get_variable(name="w1",
                              shape=[input_dim, hidden_units],
                              initializer=tf.variance_scaling_initializer(),
                              dtype=real_type))
    # bias vector
    bs.append(tf.get_variable(name="b1",
                              shape=[hidden_units],
                              initializer=tf.zeros_initializer(),
                              dtype=real_type))

    # graph
    zs.append(zs[0] @ ws[1] + bs[1])  # eq. 1, l=1

    # second hidden layer (index 2) to last (index hidden_layers)
    for l in range(1, hidden_layers):
        ws.append(tf.get_variable(name="w%d" % (l + 1),
                                  shape=[hidden_units, hidden_units],
                                  initializer=tf.variance_scaling_initializer(),
                                  dtype=real_type))
        bs.append(tf.get_variable(name="b%d" % (l + 1),
                                  shape=[hidden_units],
                                  initializer=tf.zeros_initializer(),
                                  dtype=real_type))
        zs.append(integrated_relu(zs[l]) @ ws[l + 1] + bs[l + 1])  # eq. 1, l=2,...,L-1

    # output layer (index hidden_layers+1)
    ws.append(tf.get_variable(name="w" + str(hidden_layers + 1),
                              shape=[hidden_units, 1],
                              initializer=tf.variance_scaling_initializer(),
                              dtype=real_type))
    bs.append(tf.get_variable(name="b" + str(hidden_layers + 1),
                              shape=[1],
                              initializer=tf.zeros_initializer(),
                              dtype=real_type))
    # eq. 1, l=L
    zs.append(integrated_relu(zs[hidden_layers]) @ ws[hidden_layers + 1] + bs[hidden_layers + 1])

    # result = output layer
    ys = zs[hidden_layers + 1]

    # return input layer, (parameters = weight matrices and bias vectors),
    # [all layers] and output layer
    return xs, (ws, bs), zs, ys


# compute d_output/d_inputs by (explicit) backprop in vanilla net
def backprop(
        weights_and_biases,  # 2nd output from vanilla_net()
        zs):  # 3rd output from vanilla_net()

    ws, bs = weights_and_biases
    L = len(zs) - 1

    # backpropagation, eq. 2, l=L..1
    zbar = tf.ones_like(zs[L])  # zbar_L = 1
    for l in range(L - 1, 0, -1):
        zbar = (zbar @ tf.transpose(ws[l + 1])) * tf.nn.relu(zs[l]) #tf.nn.sigmoid(zs[l])  # eq. 2

    # for l=0
    zbar = zbar @ tf.transpose(ws[1])  # eq. 2

    xbar = zbar  # xbar = zbar_0

    # dz[L] / dx
    return xbar




def vanilla_training_graph(input_dim, hidden_units, hidden_layers, seed):

    # net
    inputs, weights_and_biases, layers, predictions = vanilla_net(input_dim, hidden_units, hidden_layers, seed)

    # backprop even though we are not USING differentials for
    # training
    # we still need them to predict derivatives dy_dx
    derivs_predictions = backprop(weights_and_biases, layers)

    # placeholder for labels
    labels = tf.placeholder(shape=[None, 1], dtype=real_type)

    # loss
    loss = tf.losses.mean_squared_error(labels, predictions)

    # optimizer
    learning_rate = tf.placeholder(real_type)
    optimizer = tf.train.AdamOptimizer(learning_rate=learning_rate)

    # return all necessary
    return inputs, labels, predictions, derivs_predictions, learning_rate, loss, optimizer.minimize(loss)


# combined graph for valuation and differentiation
def twin_net(input_dim, hidden_units, hidden_layers, seed):
    # first, build the feedforward net
    xs, (ws, bs), zs, ys = vanilla_net(input_dim, hidden_units, hidden_layers, seed)

    # then, build its differentiation by backprop
    xbar = backprop((ws, bs), zs)

    # return input x, output y and differentials d_y/d_z
    return xs, ys, xbar


def diff_training_graph(
        # same as vanilla
        input_dim,
        hidden_units,
        hidden_layers,
        seed,
        # balance relative weight of values and differentials
        # loss = alpha * MSE(values) + beta * MSE(greeks, lambda_j)
        # see online appendix
        alpha,
        beta,
        lambda_j):

    # net, now a twin
    inputs, predictions, derivs_predictions = twin_net(input_dim, hidden_units, hidden_layers, seed)

    # placeholder for labels, now also derivs labels
    labels = tf.placeholder(shape=[None, 1], dtype=real_type)
    derivs_labels = tf.placeholder(shape=[None, derivs_predictions.shape[1]], dtype=real_type)

    # loss, now combined values + derivatives
    loss = alpha * tf.losses.mean_squared_error(labels, predictions) \
           + beta * tf.losses.mean_squared_error(derivs_labels * lambda_j, derivs_predictions * lambda_j)

    # optimizer, as vanilla
    learning_rate = tf.placeholder(real_type)
    optimizer = tf.train.AdamOptimizer(learning_rate=learning_rate)

    # return all necessary tensors, including derivatives
    # predictions and labels
    return inputs, labels, derivs_labels, predictions, derivs_predictions, \
        learning_rate, loss, optimizer.minimize(loss)


# basic data preparation
epsilon = 1.0e-08


def normalize_data(x_raw, y_raw, dydx_raw=None, crop=None):
    # crop dataset
    m = crop if crop is not None else x_raw.shape[0]
    x_cropped = x_raw[:m]
    y_cropped = y_raw[:m]
    dycropped_dxcropped = dydx_raw[:m] if dydx_raw is not None else None

    # normalize dataset
    x_mean = x_cropped.mean(axis=0)
    x_std = x_cropped.std(axis=0) + epsilon
    x = (x_cropped - x_mean) / x_std
    y_mean = y_cropped.mean(axis=0)
    y_std = y_cropped.std(axis=0) + epsilon
    y = (y_cropped - y_mean) / y_std

    # normalize derivatives too
    if dycropped_dxcropped is not None:
        dy_dx = dycropped_dxcropped / y_std * x_std
        # weights of derivatives in cost function = (quad) mean size
        lambda_j = 1.0 / np.sqrt((dy_dx ** 2).mean(axis=0)).reshape(1, -1)
    else:
        dy_dx = None
        lambda_j = None

    return x_mean, x_std, x, y_mean, y_std, y, dy_dx, lambda_j


# training loop for one epoch
def vanilla_train_one_epoch(  # training graph from vanilla_training_graph()
        inputs, labels, lr_placeholder, minimizer,
        # training set
        x_train, y_train,
        # params, left to client code
        learning_rate, batch_size, session):
    m, n = x_train.shape

    # minimization loop over mini-batches
    first = 0
    last = min(batch_size, m)
    while first < m:
        session.run(minimizer, feed_dict={
            inputs: x_train[first:last],
            labels: y_train[first:last],
            lr_placeholder: learning_rate
        })
        first = last
        last = min(first + batch_size, m)


def diff_train_one_epoch(inputs, labels, derivs_labels,
                         # graph
                         lr_placeholder, minimizer,
                         # training set, extended
                         x_train, y_train, dydx_train,
                         # params
                         learning_rate, batch_size, session):
    m, n = x_train.shape

    # minimization loop, now with Greeks
    first = 0
    last = min(batch_size, m)
    while first < m:
        session.run(minimizer, feed_dict={
            inputs: x_train[first:last],
            labels: y_train[first:last],
            derivs_labels: dydx_train[first:last],
            lr_placeholder: learning_rate
        })
        first = last
        last = min(first + batch_size, m)




def train(description,
          # neural approximator
          approximator,
          # training params
          reinit=True,
          epochs=100,
          # one-cycle learning rate schedule
          learning_rate_schedule=[(0.0, 1.0e-8),
                                  (0.2, 0.1),
                                  (0.6, 0.01),
                                  (0.9, 1.0e-6),
                                  (1.0, 1.0e-8)],
          batches_per_epoch=16,
          min_batch_size=256,
          # callback function and when to call it
          callback=None,  # arbitrary callable
          callback_epochs=[]):  # call after what epochs, e.g. [5, 20]

    # batching
    batch_size = max(min_batch_size, approximator.m // batches_per_epoch)

    # one-cycle learning rate sechedule
    lr_schedule_epochs, lr_schedule_rates = zip(*learning_rate_schedule)

    # reset
    if reinit:
        approximator.session.run(approximator.initializer)

    # callback on epoch 0, if requested
    if callback and 0 in callback_epochs:
        callback(approximator, 0)

    # loop on epochs, with progress bar (tqdm)
    for epoch in range(epochs): #tqdm_notebook(range(epochs), desc=description):

        # interpolate learning rate in cycle
        learning_rate = np.interp(epoch / epochs, lr_schedule_epochs, lr_schedule_rates)

        # train one epoch

        if not approximator.differential:

            vanilla_train_one_epoch(
                approximator.inputs,
                approximator.labels,
                approximator.learning_rate,
                approximator.minimizer,
                approximator.x,
                approximator.y,
                learning_rate,
                batch_size,
                approximator.session)

        else:

            diff_train_one_epoch(
                approximator.inputs,
                approximator.labels,
                approximator.derivs_labels,
                approximator.learning_rate,
                approximator.minimizer,
                approximator.x,
                approximator.y,
                approximator.dy_dx,
                learning_rate,
                batch_size,
                approximator.session)

        # callback, if requested
        if callback and epoch in callback_epochs:
            callback(approximator, epoch)

    # final callback, if requested
    if callback and epochs in callback_epochs:
        callback(approximator, epochs)


class Neural_approximator():
    def __init__(self, x_raw, y_raw,
                 dydx_raw=None):  # derivatives labels,

        self.x_raw = x_raw
        self.y_raw = y_raw
        self.dydx_raw = dydx_raw

        # tensorflow logic
        self.graph = None
        self.session = None

    def build_graph(self,
                    differential,  # differential or not
                    lam,  # balance cost between values and derivs
                    hidden_units,
                    hidden_layers,
                    weight_seed):

        # first, deal with tensorflow logic
        if self.session is not None:
            self.session.close()

        self.graph = tf.Graph()

        with self.graph.as_default():

            # build the graph, either vanilla or differential
            self.differential = differential

            if not differential:
                # vanilla

                self.inputs, \
                    self.labels, \
                    self.predictions, \
                    self.derivs_predictions, \
                    self.learning_rate, \
                    self.loss, \
                    self.minimizer \
                    = vanilla_training_graph(self.n, hidden_units, hidden_layers, weight_seed)

            else:
                # differential

                if self.dy_dx is None:
                    raise Exception("No differential labels for differential training graph")

                self.alpha = 1.0 / (1.0 + lam * self.n)
                self.beta = 1.0 - self.alpha

                self.inputs, \
                    self.labels, \
                    self.derivs_labels, \
                    self.predictions, \
                    self.derivs_predictions, \
                    self.learning_rate, \
                    self.loss, \
                    self.minimizer = diff_training_graph(self.n, hidden_units,
                                                         hidden_layers, weight_seed,
                                                         self.alpha, self.beta, self.lambda_j)

            # global initializer
            self.initializer = tf.global_variables_initializer()

        # done
        self.graph.finalize()
        self.session = tf.Session(graph=self.graph)

        # prepare for training with m examples, standard or differential
    def prepare(self,
                    m,
                    differential,
                    lam=1,  # balance cost between values and derivs
                    # standard architecture
                    hidden_units=20,
                    hidden_layers=4,
                    weight_seed=None):

            # prepare dataset
            self.x_mean, self.x_std, self.x, self.y_mean, self.y_std, self.y, self.dy_dx, self.lambda_j = \
                normalize_data(self.x_raw, self.y_raw, self.dydx_raw, m)

            # build graph
            self.m, self.n = self.x.shape
            self.build_graph(differential, lam, hidden_units, hidden_layers, weight_seed)

    def train(self,
                  description="training",
                  # training params
                  reinit=True,
                  epochs=100,
                  # one-cycle learning rate schedule
                  learning_rate_schedule=[
                      (0.0, 1.0e-8),
                      (0.2, 0.1),
                      (0.6, 0.01),
                      (0.9, 1.0e-6),
                      (1.0, 1.0e-8)],
                  batches_per_epoch=16,
                  min_batch_size=256,
                  # callback and when to call it
                  # we don't use callbacks, but this is very useful, e.g. for debugging
                  callback=None,  # arbitrary callable
                  callback_epochs=[]):  # call after what epochs, e.g. [5, 20]

        train(description,
                  self,
                  reinit,
                  epochs,
                  learning_rate_schedule,
                  batches_per_epoch,
                  min_batch_size,
                  callback,
                  callback_epochs)

    def predict_values(self, x):
                # scale
                x_scaled = (x - self.x_mean) / self.x_std
                # predict scaled
                y_scaled = self.session.run(self.predictions, feed_dict={self.inputs: x_scaled})
                # unscale
                y = self.y_mean + self.y_std * y_scaled
                return y

    def predict_values_and_derivs(self, x):
                # scale
                x_scaled = (x - self.x_mean) / self.x_std
                # predict scaled
                y_scaled, dyscaled_dxscaled = self.session.run(
                    [self.predictions, self.derivs_predictions],
                    feed_dict={self.inputs: x_scaled})
                # unscale
                y = self.y_mean + self.y_std * y_scaled
                dydx = self.y_std / self.x_std * dyscaled_dxscaled
                return y, dydx





def graph(title,
          predictions,
          xAxis,
          xAxisName,
          yAxisName,
          targets,
          sizes,
          computeRmse=False,
          weights=None,
          save=False):
    numRows = len(sizes)
    numCols = 2

    fig, ax = plt.subplots(numRows, numCols, squeeze=False)
    fig.set_size_inches(4 * numCols + 1.5, 4 * numRows)

    for i, size in enumerate(sizes):
        ax[i, 0].annotate("size %d" % size, xy=(0, 0.5),
                          xytext=(-ax[i, 0].yaxis.labelpad - 5, 0),
                          xycoords=ax[i, 0].yaxis.label, textcoords='offset points',
                          ha='right', va='center')

    ax[0, 0].set_title("Standard Neural Network")
    ax[0, 1].set_title("Differential Neural Network")

    for i, size in enumerate(sizes):
        for j, regType, in enumerate(["standard", "differential"]):

            if computeRmse:
                errors = (predictions[(regType, size)] - targets)
                if weights is not None:
                    errors /= weights
                rmse = np.sqrt((errors ** 2).mean(axis=0))
                t = "RMSE %.4f" % rmse
            else:
                t = xAxisName

            ax[i, j].set_xlabel(t)
            ax[i, j].set_ylabel(yAxisName)

            ax[i, j].plot(xAxis, predictions[(regType, size)], 'co',
                          markersize=2, markerfacecolor='white', label="Predicted")
            ax[i, j].plot(xAxis, targets, 'r.', markersize=0.5, label='Binomial Model')

            ax[i, j].legend(prop={'size': 8}, loc='upper left')

    plt.tight_layout()
    plt.subplots_adjust(top=0.9)
    plt.suptitle("% s -- %s" % (title, yAxisName), fontsize=16)
    plt.show()


if __name__ == '__main__':
    # Param setting
    seed = 1234
    sizeTrain = [1024, 8192]  # Plot predictions for each training number
    sizeTest = 1000           # Number of test observations used for predictions
    spot_cutoff = False
    showDeltas = True

    # Load generated, pathwise data
    pathwisePath = get_data_path("LSMC_pathwise.csv")
    dataPathwise = np.genfromtxt(pathwisePath, delimiter=",", skip_header=0)
    # Assigning datastructures
    dataPathwise = resample(dataPathwise, n_samples=len(dataPathwise), random_state=seed)
    """
    # resampled entire data to be sure cropping works
    # assigning datastructures
    if sizeTrain < len(dataPathwise):
        dataPathwise = resample(dataPathwise, n_samples=sizeTrain)
    """
    x_train = dataPathwise[:, 0].reshape(-1, 1)
    y_train = dataPathwise[:, 1].reshape(-1, 1)
    z_train = dataPathwise[:, 2].reshape(-1, 1)


    # Load Binomial data for reference
    binomialPath = get_data_path("binomial_unif.csv")
    dataBinomial = np.genfromtxt(binomialPath, delimiter=",", skip_header=0)
    if spot_cutoff:
        dataBinomial = dataBinomial[335:, :]
    # assigning datastructures
    dataBinomial = resample(dataBinomial, n_samples=len(dataBinomial), random_state=seed)
    """
    # resampled entire data to be sure cropping works
    if sizeTest < len(dataBinomial):
        dataBinomial = resample(dataBinomial, n_samples=sizeTest)
    """
    x_test = dataBinomial[:sizeTest, 0].reshape(-1, 1)
    y_test = dataBinomial[:sizeTest, 1].reshape(-1, 1)
    z_test = dataBinomial[:sizeTest, 2].reshape(-1, 1)

    # Learn neural approximation
    regressor = Neural_approximator(x_raw=x_train, y_raw=y_train, dydx_raw=z_train)

    sizes = sizeTrain
    weightSeed = 1234
    deltidx = 0

    predvalues = {}
    preddeltas = {}

    for size in sizes:
        print("\nsize %d" % size)
        regressor.prepare(size, False, weight_seed=weightSeed)

        regressor.train("standard training")
        predictions, deltas = regressor.predict_values_and_derivs(x_test)
        predvalues[("standard", size)] = predictions
        preddeltas[("standard", size)] = deltas[:, deltidx]

        regressor.prepare(size, True, weight_seed=weightSeed)

        regressor.train("differential training")
        predictions, deltas = regressor.predict_values_and_derivs(x_test)
        predvalues[("differential", size)] = predictions
        preddeltas[("differential", size)] = deltas[:, deltidx]


    # show predicitions
    graph(title=" ",
          predictions=predvalues,
          xAxis=x_test,
          xAxisName="",
          yAxisName="",
          targets=y_test,
          sizes=sizes,
          computeRmse=True,
          weights=None
          )

    # show deltas
    if showDeltas:
        graph(title=" ",
              predictions=preddeltas,
              xAxis=x_test,
              xAxisName="",
              yAxisName="",
              targets=z_test.reshape(-1, ),  # reshaped in order to match dimensionality
              sizes=sizes,
              computeRmse=True,
              weights=None
              )
