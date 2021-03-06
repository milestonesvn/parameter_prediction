import theano.tensor as T
from pylearn2.models import mlp
from pylearn2.space import VectorSpace
from pylearn2.utils import sharedX
from pylearn2.utils import safe_zip
from pylearn2.linear.matrixmul import MatrixMul
from collections import OrderedDict

class MLP(mlp.MLP):
    def inv_prop(self, state_above):
        self.layers[-1].output_space.validate(state_above)

        state_from_above = self.layers[-1].inv_prop(state_above)
        layer_above = self.layers[-1]
        for layer in reversed(self.layers[:-1]):
            desired_space = layer_above.input_space

            state_from_above = layer.inv_prop(
                    desired_space.format_as(state_from_above, layer.output_space))

            layer_above = layer

        return state_from_above

    @property
    def output_space(self):
        return self.layers[-1].output_space

    def get_weight_decay(self, coeff):
        return sum(layer.get_weight_decay(coeff) for layer in self.layers)

    def get_l1_weight_decay(self, coeff):
        return sum(layer.get_l1_weight_decay(coeff) for layer in self.layers)

class VectorSpaceConverter(mlp.Layer):
    def __init__(self, layer_name):
        self.layer_name = layer_name
        self._params = []

    def set_input_space(self, space):
        self.input_space = space
        self.output_space = VectorSpace(space.get_total_dimension())

    def fprop(self, state_below):
        return self.input_space.format_as(state_below, self.output_space)

    def inv_prop(self, state_above):
        return self.output_space.format_as(state_above, self.input_space)

    def get_weight_decay(self, coeff):
        return 0.0

    def get_l1_weight_decay(self, coeff):
        return 0.0

class CompositeLayer(mlp.CompositeLayer):
    @property
    def dim(self):
        return sum(layer.dim for layer in self.layers)

    def get_input_space(self):
        input_space = self.layers[0].get_input_space()
        assert all(input_space == layer.get_input_space() for layer in self.layers)
        return input_space

    def inv_prop(self, state_above):
        if not isinstance(state_above, tuple):
            expected_space = VectorSpace(self.output_space.get_total_dimension())
            state_above = expected_space.format_as(state_above, self.output_space)

        self.output_space.validate(state_above)
        return tuple(layer.inv_prop(state) for layer,state in safe_zip(self.layers, state_above))

class PretrainedLayer(mlp.PretrainedLayer):
    def fprop(self, *args, **kwargs):
        return self.layer_content.fprop(*args, **kwargs)

    def inv_prop(self, state_above):
        return self.layer_content.inv_prop(state_above)

    def get_weight_decay(self, coeff):
        return self.layer_content.get_weight_decay(coeff)

    def get_l1_weight_decay(self, coeff):
        return self.layer_content.get_weight_decay(coeff)

class ReversableLayerMixin(object):
    def inv_prop(self, state_above):
        self.output_space.validate(state_above)
        return self.transformer.lmul_T(state_above)

class Sigmoid(mlp.Sigmoid, ReversableLayerMixin):
    pass

class RectifiedLinear(mlp.RectifiedLinear, ReversableLayerMixin):
    pass

class SubsampledDictionaryLayer(mlp.Layer, ReversableLayerMixin):
    def __init__(self, dim, layer_name, dictionary):
        self.dim = dim
        self.layer_name = layer_name
        self.dictionary = dictionary

    def fprop(self, state_below):
        self.input_space.validate(state_below)

        if self.requires_reformat:
            state_below = self.input_space.format_as(state_below, self.desired_space)

        z = self.transformer.lmul(state_below)

        return z

    def set_input_space(self, space):
        self.input_space = space

        if isinstance(space, VectorSpace):
            self.requires_reformat = False
            self.input_dim = space.dim
        else:
            self.requires_reformat = True
            self.input_dim = space.get_total_dimension()
            self.desired_space = VectorSpace(self.input_dim)

        self.output_space = VectorSpace(self.dim)

        self.rng = self.mlp.rng

        # sanity checking
        assert self.dictionary.input_dim == self.input_dim
        assert self.dictionary.size >= self.dim

        indices = self.rng.permutation(self.dictionary.size)
        indices = indices[:self.dim]
        indices.sort()

        W = self.dictionary.get_subdictionary(indices)

        # dictionary atoms are stored in rows but transformers expect them to
        # be in columns.
        W = sharedX(W.T)
        W.name = self.layer_name + "_W"
        self.transformer = MatrixMul(W)

    # This is a static layer, there is no cost, no parameters, no updates, etc, etc
    def get_params(self):
        return []

    def cost(self, Y, Y_hat):
        return 0.0

    def cost_from_cost_matrix(self, cost_matrix):
        return 0.0

    def cost_matrix(self, Y, Y_hat):
        return T.zeros_like(Y)

    def get_weight_decay(self, coeff):
        return 0.0

    def get_l1_weight_decay(self, coeff):
        return 0.0
